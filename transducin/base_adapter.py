# transducin/base_adapter.py
# SPDX-License-Identifier: Apache-2.0
#
# DeviceAdapter — protocolo abstracto para adaptadores de equipos OCT.
#
# Para agregar un nuevo equipo (ej. Spectralis, Topcon):
#   1. Crear transducin/adapters/mi_equipo.py implementando DeviceAdapter
#   2. Instanciar y agregar la instancia a REGISTRY en adapter_registry.py
#   3. hot_folder_watcher detecta y procesa el nuevo formato automáticamente.
#
# Contrato mínimo:
#   - can_handle(path)       → bool     ¿puede este adaptador procesar el archivo?
#   - extract(path)          → OCTClinicalData  mediciones clínicas
#   - to_dicom(path, cd, out)→ list[Path]       archivo nativo → DICOM imagen(es)
#   - vendor                 → str      nombre legible del fabricante
#   - extensions             → frozenset[str]   extensiones soportadas (lowercase)

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from transducin.clinical_data import OCTClinicalData


class DeviceAdapter(ABC):
    """Protocolo abstracto para conversores de equipos OCT propietarios → DICOM SR."""

    # ── obligatorio implementar ───────────────────────────────────────────────

    @property
    @abstractmethod
    def vendor(self) -> str:
        """Nombre legible del fabricante / modelo, ej. 'Optovue Revo FC130'."""

    @property
    @abstractmethod
    def extensions(self) -> frozenset[str]:
        """Extensiones de archivo soportadas, en minúsculas. Ej: frozenset({'.opt'})."""

    @property
    @abstractmethod
    def adapter_id(self) -> str:
        """Identificador único de cadena para este adaptador, ej. 'revo_opt'.
        Usado por hot_folder_watcher para mapear al proceso correcto.
        """

    @abstractmethod
    def extract(self, path: Path) -> OCTClinicalData:
        """Extrae mediciones clínicas del archivo nativo.

        Args:
            path: ruta al archivo del equipo (debe existir).

        Returns:
            OCTClinicalData poblado con los campos disponibles.
        """

    @abstractmethod
    def to_dicom(
        self,
        path: Path,
        clinical_data: OCTClinicalData,
        output_dir: Path,
    ) -> list[Path]:
        """Convierte el archivo nativo a una o más imágenes DICOM.

        Para archivos que ya son DICOM (ej. .EX.DCM de Cirrus), retorna [path].
        Para formatos propietarios (ej. .opt), genera los .dcm correspondientes.

        Args:
            path:          ruta al archivo nativo.
            clinical_data: resultado de extract() — provee noel_id, laterality, etc.
            output_dir:    carpeta de destino para los .dcm generados.

        Returns:
            Lista de rutas a los archivos .dcm generados (puede estar vacía si falla).
        """

    # ── con implementación por defecto ───────────────────────────────────────

    def can_handle(self, path: Path) -> bool:
        """Retorna True si este adaptador puede procesar el archivo.

        Implementación base: compara `path.suffix.lower()` contra `self.extensions`.
        Las subclases pueden sobreescribir para lógica más sofisticada
        (ej. inspección de magic bytes para archivos ambiguos).
        """
        return path.suffix.lower() in self.extensions

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} vendor={self.vendor!r} exts={self.extensions}>"
