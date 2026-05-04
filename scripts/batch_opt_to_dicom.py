"""Batch reprocessing: .opt → DICOM images + SR, sin Orthanc, sin mover archivos.

Uso (directorio único ad-hoc):
  .venv/bin/python scripts/batch_opt_to_dicom.py --input DIR --output DIR

Uso (batch multi-directorio con JOBS):
  Edita la lista JOBS en este script y ejecuta sin argumentos.
"""
from __future__ import annotations

import argparse
import logging
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pydicom
from pydicom.uid import generate_uid

from transducin.opt_extractor import extract_from_opt, build_noel_index
from transducin.noel_resolver import resolve_patient_demographics
from transducin.revo_opt_reader import opt_to_dicom
from transducin.sr_builder import build_sr

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Example JOBS — edit for your local paths, or use --input/--output instead:
# JOBS: list[tuple[list[Path], Path]] = [
#     ([Path("/path/to/input")], Path("/path/to/output")),
# ]
JOBS: list[tuple[list[Path], Path]] = []


def process_one(opt_path: Path, output_dir: Path, noel_index: dict) -> bool:
    logger.info("── %s", opt_path.name)

    # 1. Extraer metadatos clínicos del .opt
    try:
        cd = extract_from_opt(opt_path, noel_index=noel_index)
    except Exception as e:
        logger.error("  extract FALLO: %s", e)
        return False

    # 2. Resolver demographics (SOCT.db Tier 1, filename index Tier 2)
    demo = resolve_patient_demographics(
        patient_name=cd.patient_name or "",
        patient_dob=cd.patient_dob,
        filename_index=noel_index,
    )
    if not cd.noel_id:
        cd.noel_id = demo["noel_id"]
    if not cd.patient_dob and demo["patient_dob"]:
        cd.patient_dob = demo["patient_dob"]
    if demo["patient_name"] and "^" in demo["patient_name"]:
        cd.patient_name = demo["patient_name"]
    patient_sex = demo["patient_sex"]

    lat_str  = {"R": "OD", "L": "OS"}.get(cd.laterality, "")
    type_str = (cd.study_type or "").replace("_", " ").title()
    study_desc = f"Revo FC130 {type_str} {lat_str}".strip()

    logger.info("  noel=%s  name=%s  lat=%s  type=%s",
                cd.noel_id or "(none)", cd.patient_name or "(none)",
                cd.laterality, cd.study_type)

    # 3. Imágenes DICOM
    dcm_paths: list[Path] = []
    if cd.study_type == "fundus":
        logger.info("  Fundus fotográfico — sin OCT, omitido.")
        return True
    if cd.study_type not in ("bmetr", "biometry"):
        try:
            img_dir = output_dir
            img_dir.mkdir(parents=True, exist_ok=True)
            noel_id = cd.noel_id if (cd.noel_id and cd.noel_id != "UNKNOWN") else ""
            assert noel_id != "UNKNOWN", "UNKNOWN escapó el filtro"
            dcm_paths = opt_to_dicom(
                opt_path, img_dir,
                noel_id=noel_id,
                study_date=cd.study_date or "",
                study_time=cd.study_time or "",
                laterality=cd.laterality or "",
                patient_name=cd.patient_name or "",
                patient_dob=cd.patient_dob or "",
                patient_sex=patient_sex,
                study_type=cd.study_type or "",
                study_description=study_desc,
            )
            for p in dcm_paths:
                logger.info("  DCM: %s", p.name)
        except Exception as e:
            logger.error("  opt_to_dicom FALLO: %s", e)
            return False
    else:
        logger.info("  Biometry — sin imágenes.")

    # 4. SR
    if cd.study_type == "unknown" and not cd.has_measurements():
        logger.info("  SR omitido: tipo unknown sin mediciones.")
        return True

    try:
        ref_ds = pydicom.dcmread(str(dcm_paths[0])) if dcm_paths else None
        study_uid = ref_ds.StudyInstanceUID if ref_ds else str(generate_uid())

        output_dir.mkdir(parents=True, exist_ok=True)
        stem = opt_path.stem.replace(" ", "_")
        sr_path = output_dir / f"{stem}_SR.dcm"

        build_sr(cd, reference_dataset=ref_ds, output_path=sr_path,
                 study_instance_uid=study_uid)
        logger.info("  SR: %s", sr_path.name)
    except Exception as e:
        logger.error("  SR FALLO: %s", e)
        return False

    return True


def run_job(source_dirs: list[Path], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Construir NOEL index combinado de todos los directorios fuente
    noel_index: dict = {}
    for src in source_dirs:
        if src.exists():
            noel_index.update(build_noel_index(src))

    opt_files = []
    for src in source_dirs:
        if src.exists():
            opt_files.extend(sorted(src.rglob("*.opt")))
        else:
            logger.warning("Directorio no encontrado: %s", src)

    logger.info("=== %s — %d archivos .opt ===", output_dir.name.upper(), len(opt_files))

    ok = err = 0
    for f in opt_files:
        result = process_one(f, output_dir, noel_index)
        if result:
            ok += 1
        else:
            err += 1

    logger.info("=== %s: OK=%d  ERR=%d  total=%d ===\n",
                output_dir.name.upper(), ok, err, ok + err)


def main() -> None:
    parser = argparse.ArgumentParser(description="Transducin batch .opt → DICOM")
    parser.add_argument("--input",  type=Path, help="Single source directory")
    parser.add_argument("--output", type=Path, help="Output directory (required with --input)")
    args = parser.parse_args()

    if args.input:
        if not args.output:
            parser.error("--output is required when --input is specified")
        run_job([args.input.expanduser()], args.output.expanduser())
    else:
        for source_dirs, output_dir in JOBS:
            run_job(source_dirs, output_dir)


if __name__ == "__main__":
    main()
