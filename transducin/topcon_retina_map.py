# transducin/topcon_retina_map.py
# SPDX-License-Identifier: Apache-2.0
#
# Mapeo VERSIONADO y EXPERIMENTAL de los contornos RETINA_1..4 del .fda del Topcon 3D OCT-1000
# Mark II (software 3DOCT-1000.exe 3.21) a superficies anatómicas.
#
# Es una HIPÓTESIS: procede de revisión visual de un especialista (1 lector) y de evidencia
# bibliográfica sobre nomenclatura/orden; la correspondencia con el campo binario del archivo NO
# está confirmada. Este módulo no lo usa el lector ni el adapter de producción (FDAReader sigue
# tratando RETINA_n como contornos sin identificar). Solo lo consume la ruta explícita
# `experimental` (transducin.topcon_experimental).
#
# NO modificar RETINA_2/RETINA_3 de forma automática: cualquier cambio de identidad implica una
# nueva versión de este mapeo y una nueva revisión.

from __future__ import annotations

from types import MappingProxyType

MAP_VERSION = "0.1.0"

SUPPORTED_MODEL = "3DOCT-1000MK2"  # @HW_INFO_03.model_name
EXPECTED_AXIAL_UM = 3.5  # 3.5 µm/px: PARAM_SCAN_04 y la bibliografía (Yang 2010, 2011)

HYPOTHESIS_RETINA_MAP = MappingProxyType(
    {
        "version": MAP_VERSION,
        "status": "hypothesis",
        "binary_correspondence_confirmed": False,
        "device_model": SUPPORTED_MODEL,
        "software_stamp": "3.21.000.00",
        "mapping": MappingProxyType(
            {
                "RETINA_1": "ILM",
                "RETINA_2": "IS/OS",
                "RETINA_3": "OS/RPE",
                "RETINA_4": "BM/Choroid",
            }
        ),
        # Solo estos dos contornos se usan para el espesor experimental ILM→BM.
        "used_for_thickness": ("RETINA_1", "RETINA_4"),
        "identity_source": (
            "revisión visual del especialista (1 lector, 2026-11-04): ILM, IS/OS, OS/RPE, BM confirmadas visualmente",
            (
                "bibliografía: Yang et al. 2011 (PMC3184859) describe exactamente 4 fronteras con la misma "
                "nomenclatura y orden en el 3D OCT-1000; TABS, Yang et al. 2010 (PMC3101081), 9 fronteras"
            ),
        ),
        "not_established": (
            "que el software 3.21 del equipo use los algoritmos de esos artículos (no se menciona el formato .fda)",
            "la correspondencia binaria RETINA_n ↔ superficie (ningún documento de Topcon la define)",
        ),
    }
)
