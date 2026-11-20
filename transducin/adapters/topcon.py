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
from transducin.adapters.spectralis import _device_patient_id, _measurements_from_reader
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

    def __init__(self, *, experimental: bool = False) -> None:
        # `experimental` habilita SOLO experimental_thickness() (3D OCT-1000 Mark II); no cambia extract().
        self.experimental = experimental

    def experimental_thickness(self, path: Path):
        """Espesor ILM→BM experimental con RETINA_1/RETINA_4 (ver transducin.topcon_experimental).

        Requiere TopconFdaAdapter(experimental=True). No alimenta cmt_um ni el SR.
        """
        if not self.experimental:
            raise RuntimeError("espesor experimental deshabilitado: crea TopconFdaAdapter(experimental=True)")
        from transducin.topcon_experimental import experimental_thickness_from_file

        return experimental_thickness_from_file(path)

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
        cd = OCTClinicalData(
            noel_id="",  # alias NOEL: lo resuelve el llamador (p. ej. prefijo del nombre de archivo)
            device_patient_id=_device_patient_id(reader),  # ADR-026: identificador del equipo, sin modificar
            vendor="topcon_dri_oct",
            **meas,
        )
        if meas["extraction_confidence"] != "unknown":  # hubo alguna medición calculada
            cd.measurement_provenance = "vendor_contour_derived"
        unidentified = getattr(reader, "unidentified_contours", None)
        if isinstance(unidentified, list) and unidentified and meas["cmt_um"] is None:
            cd.add_note(
                f"UNIDENTIFIED: contornos de segmentación presentes {unidentified} sin identidad "
                "anatómica documentada; no se calculan espesores"
            )
        return cd

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
