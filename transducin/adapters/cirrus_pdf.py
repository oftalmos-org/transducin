# transducin/adapters/cirrus_pdf.py
# SPDX-License-Identifier: Apache-2.0
#
# DeviceAdapter para reportes PDF del Zeiss Cirrus HD-OCT
# (Macular Thickness Report, ONH+RNFL Report — exportados desde el equipo).

from __future__ import annotations

from pathlib import Path

from transducin.base_adapter import DeviceAdapter
from transducin.clinical_data import OCTClinicalData


class CirrusPdfAdapter(DeviceAdapter):
    """Adaptador para reportes .pdf del Zeiss Cirrus HD-OCT."""

    @property
    def vendor(self) -> str:
        return "Zeiss Cirrus HD-OCT (PDF report)"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".pdf"})

    @property
    def adapter_id(self) -> str:
        return "cirrus_pdf"

    def extract(self, path: Path) -> OCTClinicalData:
        raise NotImplementedError(
            "CirrusPdfAdapter.extract() no está disponible en modo de archivo único. "
            "El pipeline PDF extrae datos mediante OCR en process_cirrus_pdf_file()."
        )

    def to_dicom(
        self,
        path: Path,
        clinical_data: OCTClinicalData,
        output_dir: Path,
    ) -> list[Path]:
        # Los PDFs no tienen conversión imagen directa; el SR se genera desde
        # los valores extraídos por OCR, no desde la imagen.
        return []
