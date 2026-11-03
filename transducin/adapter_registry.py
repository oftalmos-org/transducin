# transducin/adapter_registry.py
# SPDX-License-Identifier: Apache-2.0
#
# Registro global de DeviceAdapters.
#
# Para agregar soporte para un nuevo equipo (ej. Spectralis, Topcon):
#   1. Crear transducin/adapters/mi_equipo.py implementando DeviceAdapter
#   2. Agregar una instancia a REGISTRY aquí

from __future__ import annotations

from pathlib import Path
from typing import Optional

from transducin.base_adapter import DeviceAdapter
from transducin.adapters.revo import RevoOptAdapter
from transducin.adapters.cirrus import CirrusDcmAdapter
from transducin.adapters.cirrus_pdf import CirrusPdfAdapter

REGISTRY: list[DeviceAdapter] = [
    RevoOptAdapter(),
    CirrusDcmAdapter(),
    CirrusPdfAdapter(),
]

# Extensiones agregadas de todos los adapters registrados.
# hot_folder_watcher usa esto para filtrar eventos del filesystem.
ALL_WATCHED_EXTENSIONS: frozenset[str] = frozenset(
    ext for adapter in REGISTRY for ext in adapter.extensions
)


def find_adapter(path: Path) -> Optional[DeviceAdapter]:
    """Retorna el primer adapter del REGISTRY que puede manejar el archivo,
    o None si ninguno lo reconoce.
    """
    for adapter in REGISTRY:
        if adapter.can_handle(path):
            return adapter
    return None
