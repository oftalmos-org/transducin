# transducin/adapters/cirrus.py
# SPDX-License-Identifier: Apache-2.0
#
# DeviceAdapter para Zeiss Cirrus HD-OCT — archivos .EX.DCM (suffix .dcm).
#
# Nota de diseño: la extracción Cirrus opera a nivel de directorio (un examen
# contiene múltiples .EX.DCM), requiere NOEL ID previo, y gestiona
# desofuscación XOR+JP2K con carl_deobfuscator.  Por eso extract() aquí
# levanta NotImplementedError y hot_folder_watcher despacha a
# process_czm_file() cuando adapter_id == "cirrus_dcm".  Las subclases que
# quieran sobreescribir extract() pueden hacerlo.

from __future__ import annotations

from pathlib import Path

from transducin.base_adapter import DeviceAdapter
from transducin.clinical_data import OCTClinicalData


class CirrusDcmAdapter(DeviceAdapter):
    """Adaptador para archivos .EX.DCM del Zeiss Cirrus HD-OCT."""

    @property
    def vendor(self) -> str:
        return "Zeiss Cirrus HD-OCT"

    @property
    def extensions(self) -> frozenset[str]:
        # .EX.DCM tiene suffix .dcm; carl_deobfuscator distingue dentro del pipeline
        return frozenset({".dcm"})

    @property
    def adapter_id(self) -> str:
        return "cirrus_dcm"

    def can_handle(self, path: Path) -> bool:
        # Acepta cualquier .dcm — process_czm_file filtra por SOP class internamente
        return path.suffix.lower() == ".dcm"

    def extract(self, path: Path) -> OCTClinicalData:
        # La extracción Cirrus es directorio-level; se delega a process_czm_file.
        # Para uso directo fuera del watcher, llamar cirrus_extractor.extract_from_exam().
        raise NotImplementedError(
            "CirrusDcmAdapter.extract() no está disponible en modo de archivo único. "
            "Usar cirrus_extractor.extract_from_exam(exam_dir, noel_id) directamente."
        )

    def to_dicom(
        self,
        path: Path,
        clinical_data: OCTClinicalData,
        output_dir: Path,
    ) -> list[Path]:
        # El .EX.DCM ya es DICOM (posiblemente ofuscado); la desofuscación
        # la maneja process_czm_file con carl_deobfuscator.
        return [path]
