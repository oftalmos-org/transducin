# transducin/adapters/topcon.py
# SPDX-License-Identifier: Apache-2.0
#
# DeviceAdapter para Topcon DRI OCT Triton / Atlantis — archivos .fda y .fds.
#
# Usa el reader vendorizado en transducin/readers/fda_reader.py (MIT, basado en
# marksgraham/OCT-Converter). read_thickness_map() lee @CONTOUR_INFO y calcula
# CMT/ETDRS/mRNFL/mGCIPL; no requiere el paquete oct_converter.

from __future__ import annotations

from pathlib import Path

# Reutiliza los helpers de mapeo del adaptador Spectralis
from transducin.adapters.spectralis import _measurements_from_reader
from transducin.base_adapter import DeviceAdapter
from transducin.clinical_data import OCTClinicalData
from transducin.readers.fda_reader import topcon_reader as _topcon_reader_factory


def _topcon_reader(path: Path):
    """Instancia el reader adecuado según la extensión del archivo."""
    return _topcon_reader_factory(path)


class TopconFdaAdapter(DeviceAdapter):
    """Adaptador para archivos .fda y .fds del Topcon DRI OCT.

    Extrae CMT, ETDRS, mRNFL, mGCIPL (macular) o pRNFL (circle scan) desde
    @CONTOUR_INFO con el reader vendorizado. Sin segmentación, o sin lateralidad,
    retorna medidas None (degradación elegante).
    """

    @property
    def vendor(self) -> str:
        return "Topcon DRI OCT"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".fda", ".fds"})

    @property
    def adapter_id(self) -> str:
        return "topcon"

    def extract(self, path: Path) -> OCTClinicalData:
        reader = _topcon_reader(path)

        laterality: str | None = None
        try:
            volumes = reader.read_oct_volume()
            if volumes:
                lat = (getattr(volumes[0], "laterality", None) or "").upper()
                if lat in ("L", "R"):
                    laterality = lat
        except Exception:
            pass

        meas = _measurements_from_reader(reader, laterality)
        return OCTClinicalData(
            noel_id="",  # resuelto desde filename en hot_folder_watcher
            vendor="topcon_dri_oct",
            **meas,
        )

    def to_dicom(
        self,
        path: Path,
        clinical_data: OCTClinicalData,
        output_dir: Path,
    ) -> list[Path]:
        """Convierte volumen OCT .fda/.fds a archivos DICOM OphthalmicTomographyImage.

        TODO: implementación pendiente de archivos de muestra para validación.
        Retorna lista vacía — el SR se genera igual desde clinical_data.
        """
        return []
