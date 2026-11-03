# transducin/adapters/topcon.py
# SPDX-License-Identifier: Apache-2.0
#
# DeviceAdapter para Topcon DRI OCT Triton / Atlantis — archivos .fda y .fds.
#
# Usa oct-converter (oftalmos-org/oct-converter fork) para leer el binario.
# Si el fork no está instalado (solo oct-converter upstream), extract() devuelve
# OCTClinicalData con medidas None en lugar de fallar.

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from transducin.base_adapter import DeviceAdapter
from transducin.clinical_data import ETDRSGrid, OCTClinicalData, RNFLSectors

# Reutiliza los helpers de mapeo del adaptador Spectralis
from transducin.adapters.spectralis import _to_etdrs_grid, _to_rnfl_sectors


def _topcon_reader(path: Path):
    """Instancia el reader adecuado según la extensión del archivo."""
    suffix = path.suffix.lower()
    if suffix == ".fda":
        from oct_converter.readers import FDA  # type: ignore[import]
        return FDA(str(path))
    if suffix == ".fds":
        try:
            from oct_converter.readers import FDS  # type: ignore[import]
            return FDS(str(path))
        except ImportError:
            # FDS no disponible en todas las versiones de oct-converter
            from oct_converter.readers import FDA  # type: ignore[import]
            return FDA(str(path))
    raise ValueError(f"Extensión no soportada por TopconFdaAdapter: {suffix}")


class TopconFdaAdapter(DeviceAdapter):
    """Adaptador para archivos .fda y .fds del Topcon DRI OCT.

    Requiere oftalmos-org/oct-converter (fork con read_thickness_map()) para
    extracción de CMT/ETDRS/RNFL. Con oct-converter upstream, extract() retorna
    metadata básica y medidas None (degradación elegante).
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
        try:
            reader = _topcon_reader(path)
        except ImportError as exc:
            raise ImportError(
                "oct-converter no instalado. Instalar con: "
                "pip install 'oct-converter @ git+https://github.com/oftalmos-org/oct-converter.git@main'"
            ) from exc

        # ── Metadata básica desde el volumen OCT ──────────────────────────────
        laterality: Optional[str] = None
        study_date: str = ""
        study_time: str = ""
        study_type: str = "macular"

        try:
            volumes = reader.read_oct_volume()
            if volumes:
                vol = volumes[0]
                lat = (getattr(vol, "laterality", None) or "").upper()
                if lat in ("L", "R"):
                    laterality = lat
        except Exception:
            pass

        # ── Medidas cuantitativas (solo disponibles en el fork) ───────────────
        cmt_um: Optional[float] = None
        etdrs_grid: Optional[ETDRSGrid] = None
        rnfl: Optional[RNFLSectors] = None
        confidence = "unknown"

        if hasattr(reader, "read_thickness_map"):
            try:
                maps = reader.read_thickness_map()
                if maps:
                    m = maps[0]
                    if laterality is None:
                        lat = (getattr(m, "laterality", None) or "").upper()
                        if lat in ("L", "R"):
                            laterality = lat
                    cmt_um = getattr(m, "cmt_um", None)
                    etdrs_grid = _to_etdrs_grid(getattr(m, "etdrs_sectors", None))
                    rnfl = _to_rnfl_sectors(getattr(m, "rnfl_sectors", None))
                    scan_date: Optional[datetime] = getattr(m, "scan_date", None)
                    if scan_date:
                        study_date = scan_date.strftime("%Y%m%d")
                        study_time = scan_date.strftime("%H%M%S")
                    if cmt_um is not None:
                        confidence = "confirmed"
                    else:
                        confidence = "assumed"
            except Exception:
                pass

        return OCTClinicalData(
            noel_id="",  # resuelto desde filename en hot_folder_watcher
            vendor="topcon_dri_oct",
            laterality=laterality or "",
            study_type=study_type,
            study_date=study_date,
            study_time=study_time,
            cmt_um=cmt_um,
            etdrs_grid=etdrs_grid,
            rnfl=rnfl,
            sqi_mean=None,
            extraction_confidence=confidence,
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
