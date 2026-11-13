# transducin/adapters/revo.py
# SPDX-License-Identifier: Apache-2.0
#
# DeviceAdapter para Optovue / Nidek Revo FC130 — archivos .opt

from __future__ import annotations

import re
from pathlib import Path

from transducin.base_adapter import DeviceAdapter
from transducin.clinical_data import OCTClinicalData

# Mismo patrón que usa revo_opt_reader.opt_to_dicom() para el prefijo NOEL del archivo.
_NOEL_STEM_RE = re.compile(r"^([A-Z]{4}\d{8})_")


class RevoOptAdapter(DeviceAdapter):
    """Adaptador para archivos .opt del Optovue / Nidek Revo FC130."""

    @property
    def vendor(self) -> str:
        return "Optovue / Nidek Revo FC130"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".opt"})

    @property
    def adapter_id(self) -> str:
        return "revo_opt"

    def extract(self, path: Path) -> OCTClinicalData:
        from transducin.opt_extractor import extract_from_opt

        cd = extract_from_opt(path)
        if not cd.noel_id:
            # Prefijo NOEL explícito que el parser del nombre no reconoció: es el mismo
            # que opt_to_dicom() escribe en la imagen; así imagen y SR no divergen.
            m = _NOEL_STEM_RE.match(path.stem)
            if m:
                cd.noel_id = m.group(1)
        return cd

    def to_dicom(
        self,
        path: Path,
        clinical_data: OCTClinicalData,
        output_dir: Path,
    ) -> list[Path]:
        from transducin.clinical_data import study_description_label
        from transducin.revo_opt_reader import opt_to_dicom

        return opt_to_dicom(
            filepath=path,
            output_dir=output_dir,
            noel_id=clinical_data.noel_id or "",
            # ADR-026: PatientID efectivo (alias NOEL → identificador de la fuente → vacío).
            patient_id=clinical_data.patient_id,
            study_date=clinical_data.study_date or "",
            laterality=clinical_data.laterality or "",
            patient_name="",
            patient_dob="",
            patient_sex="",
            study_type=clinical_data.study_type or "",
            study_description=study_description_label(clinical_data.study_type or "", clinical_data.laterality or ""),
            study_time=clinical_data.study_time or "",
        )
