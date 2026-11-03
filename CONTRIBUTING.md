# Contribuir a Transducin

Transducin es el pipeline OCT → DICOM SR de RetinaOS. Acepta contribuciones en
forma de nuevos `DeviceAdapter`s, mejoras al pipeline existente, y correcciones de bugs.

---

## Configuración del entorno de desarrollo

```bash
# Clonar el repositorio
git clone https://github.com/oftalmos-org/transducin.git
cd transducin

# Crear y activar venv (Python 3.11+)
python3.11 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Instalar en modo editable con dependencias de desarrollo
pip install -e ".[dev]"

# Ejecutar la suite de tests
python -m pytest tests/ -v
```

---

## Cómo añadir soporte para un nuevo equipo OCT

Transducin usa el protocolo `DeviceAdapter` para encapsular la lógica de cada equipo.
Para añadir un nuevo dispositivo:

### 1. Crear `transducin/adapters/mi_equipo.py`

```python
from pathlib import Path
from transducin.base_adapter import DeviceAdapter
from transducin.clinical_data import OCTClinicalData


class MiEquipoAdapter(DeviceAdapter):
    @property
    def vendor(self) -> str:
        return "Fabricante NombreEquipo"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".ext1", ".ext2"})  # extensiones del formato propietario

    @property
    def adapter_id(self) -> str:
        return "mi_equipo"  # snake_case, único en el registro

    def extract(self, path: Path) -> OCTClinicalData:
        # Parsear el binario propietario y retornar los datos clínicos.
        # Ver adapters/spectralis.py y adapters/revo.py como referencia.
        ...

    def to_dicom(self, path: Path, clinical_data: OCTClinicalData,
                 output_dir: Path) -> list[Path]:
        # Convertir el volumen OCT a archivos DICOM OphthalmicTomographyImage.
        # Si la conversión no está implementada, retornar [].
        return []
```

### 2. Registrar en `transducin/adapter_registry.py`

```python
from transducin.adapters.mi_equipo import MiEquipoAdapter

REGISTRY: list[DeviceAdapter] = [
    ...
    MiEquipoAdapter(),
]
```

### 3. Añadir entrada en `_ADAPTER_DISPATCH` de `hot_folder_watcher.py`

```python
_ADAPTER_DISPATCH: dict[str, Any] = {
    ...
    "mi_equipo": lambda p, o, c: process_generic_oct_file(p, o, do_cstore=c,
                                                           adapter=MiEquipoAdapter()),
}
```

### 4. Escribir tests en `tests/test_adapters_mi_equipo.py`

Los tests deben usar mocks — **nunca archivos con datos de pacientes reales**.
Consultar `tests/test_adapters_d1d2.py` como plantilla.

---

## PatientID y NOEL ID

Transducin usa `patient_id` como concepto genérico para el identificador del paciente
en DICOM (`PatientID 0010,0020`).

**NOEL ID** es la implementación de RetinaOS:
```
NOEL_ID = apellido_paterno[:2].upper() + apellido_materno[0].upper() + nombre[0].upper() + YYYYMMDD
# Ejemplo: Jesús Noel Jaurrieta Hinojos, 31 ago 1987 → JAHJ19870831
```

Patrón: `^[A-Z]{3,4}\d{8}$`

**Para clínicas que usan un formato diferente** (MRN, historia clínica, etc.):
- Cualquier string puede usarse como `patient_id` en los archivos generados
- El nombre del archivo de entrada debe seguir el patrón `PATIENTID_*.ext`
  para que el pipeline lo extraiga automáticamente
- Ejemplo con MRN: `12345678_OD_20261101_scan.e2e`
- La validación del formato se puede relajar configurando `TRANSDUCIN_PATIENT_ID_PATTERN`
  (variable de entorno, soporte futuro)

---

## Reglas PHI para fixtures de test

Todos los archivos en `tests/fixtures/` deben ser **100% sintéticos** — sin datos
de pacientes reales.

- **No commitear** archivos `.opt`, `.dcm`, `.e2e`, `.fda`, `.fds` reales
- **No commitear** CSVs o JSONs con NOEL IDs, nombres, fechas de nacimiento reales
- Las IDs de paciente en fixtures sintéticos deben seguir el patrón `SILT*` o `TEST*`
  (ejemplo: `SILS19870101`, `TESTP20000101`)
- Los archivos de validación con datos reales viven fuera del repo, en
  `~/Developer/RetinaDev/validation_outputs/`

---

## Convenciones de commit

Seguimos [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(D1): add Spectralis .e2e adapter with thickness map extraction
fix(pipeline): handle missing NOEL prefix in .fda filenames
docs: update device support matrix in README
test: add mock-based tests for TopconFdaAdapter
```

---

## Readers vendorizados (Spectralis / Topcon)

El soporte Spectralis/Topcon usa código vendorizado de
[marksgraham/OCT-Converter](https://github.com/marksgraham/OCT-Converter) (MIT)
en `transducin/readers/`:

```
transducin/readers/e2e_reader.py  — parser Spectralis .e2e
transducin/readers/fda_reader.py  — parser Topcon .fda/.fds
transducin/readers/_e2e_binary.py — structs binarios Spectralis
transducin/readers/_fda_binary.py — structs binarios Topcon
```

Sin dependencia externa de oct-converter; solo se requiere `construct>=2.10`
(ya incluida en `pyproject.toml`).

Ver [ADR-022](../01-Architecture/ADRs/ADR-022-oct-converter-fork.md) para la
decisión de arquitectura completa.

La extracción de medidas cuantitativas (CMT, ETDRS, RNFL) se activará cuando
lleguen archivos `.e2e`/`.fda` de muestra para validar los offsets de chunks
propietarios del fabricante. Hasta entonces, `read_thickness_map()` devuelve
`[]` y los SRs se generan sin medidas (comportamiento documentado).

---

## Licencia

Apache-2.0. Al contribuir aceptas que tu código se distribuye bajo esa licencia.
