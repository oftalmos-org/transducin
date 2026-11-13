# OCT-Converter 0.7.0 — problemas encontrados y su impacto en Transducin

Fecha: 2026-11-04 · Versión revisada: `oct-converter 0.7.0` (MIT; `marksgraham/OCT-Converter`).
No se modificó la librería. Las correcciones son propias (en Transducin) y **no se ha reportado nada
upstream** (abrir issues es una acción externa; los borradores están al final).

## Resumen de impacto

| Ruta de Transducin | ¿Usa `oct_converter`? | ¿Afectada? |
|---|---|---|
| Adapters D1 (Spectralis) y D2 (Topcon), `readers/*` | **No** (lectores vendorizados) | No |
| Revo `.opt`, Cirrus `.dcm`/PDF | No | No |
| `transducin/boct_converter.py` (CLI aparte; **no** está en el registro de adapters) | Sí (`create_dicom_from_boct`) | **Sí**: problemas 1 y 2 |
| `spectralis_extractor.extract_from_e2e`, `topcon_fda_extractor.extract_from_fda` (extractores legados) | Sí (lectores E2E/FDA) | **Sí** (FDA): problema 3 |
| Test de paridad `test_e2e_parity_with_legacy_extractor` | Sí (opcional, se omite si falta) | No |

Los dos problemas pedidos son el 1 (PixelSpacing) y el 2 (lateralidad). El 3 y el 4 se encontraron después.

## 1. PixelSpacing y SliceThickness inventados en el DICOM de Bioptigen

- **Dónde:** `oct_converter/dicom/boct_meta.py:64-65`: `pixel_spacing = [0.02, 0.02]`,
  `slice_thickness = 0.2`, ambos con el comentario `# Placeholder value`.
- **Evidencia:** con el `.OCT` real de MATLAB Central (100 B-scans de 1000 × 1024) el DICOM resultante trae
  `PixelSpacing = [0.02, 0.02] mm` y `SliceThickness = 0.2 mm`; `BOCT.read_oct_volume()` devuelve `pixel_spacing = None`
  (el archivo no trae calibración). 20 µm/px axial no es plausible para este equipo.
- **Alcance en la librería:** también hay valores provisionales en `poct_meta.py:66` (`slice_thickness = 0.02`,
  Optovue), valores fijos `[1, 1]` en `dicom.py:591,599` (conversión FDA) y `[0.002, 0.002]` en `img_meta.py:90`
  (Zeiss `.img`). **No se verificó** si estos tres últimos son reales o provisionales. Los conversores E2E/FDA/FDS
  usan los valores del archivo.
- **Afecta a:** metadatos y **geometría** del DICOM generado por `boct_converter.py`; cualquier visor o cálculo que use
  `PixelSpacing` para medir daría distancias falsas. **No afecta** a imágenes (píxeles), a las mediciones de los adapters
  ni a Revo/Cirrus.
- **Corrección propia:** `boct_converter.annotate_boct_dicom()`: sin calibración explícita deja el DICOM pero escribe
  en `ImageComments` «PixelSpacing SIN CALIBRAR … no usar para medir» (y lo mismo para `SliceThickness`); con
  `--pixel-spacing-mm` / `--slice-thickness-mm` reemplaza los valores y lo registra. No se inventa ningún otro valor.
- **Pendiente:** decidir si, sin calibración, conviene eliminar los dos campos (puede afectar a la conformidad del IOD;
  `dciodvfy` no está instalado para comprobarlo).

## 2. Lateralidad deducida del nombre del archivo con una expresión frágil

- **Dónde:** `oct_converter/readers/boct.py:139-155` (`get_laterality_from_filename`):
  `re.search(r"O[D|S]", filename)`. Sin límites de palabra, el primer `OD`/`OS` en cualquier parte del nombre gana, y la
  clase `[D|S]` incluye el carácter `|`.
- **Evidencia (ejecutada):**

| Nombre | oct-converter | Correcto |
|---|---|---|
| `Z506_OS_V_1x1.OCT` | `L` | `L` |
| `MODEL_OS_scan.OCT` | **`R`** (el «OD» de MODEL) | `L` |
| `ROSE_OD_scan.OCT` | **`L`** (el «OS» de ROSE) | `R` |
| `SUBJ_POSITION_OD.OCT` | **`L`** (el «OS» de POSITION) | `R` |
| `OS_OD.OCT` | `L` (ambigua, escoge una) | desconocida |
| `scan_left_eye.OCT` | `` | desconocida |

- **Además:** el `.OCT` de Bioptigen **no contiene** la lateralidad; toda la lateralidad de ese DICOM sale del nombre.
- **Afecta a:** metadatos (`ImageLaterality`, `Laterality`) del DICOM de `boct_converter.py`: riesgo de **ojo invertido**,
  sin aviso. **No afecta** a los adapters D1/D2 (leen la lateralidad del propio archivo: E2E chunk 3, FDA `@CAPTURE_INFO`)
  ni a Revo/Cirrus.
- **Corrección propia:** `laterality_from_filename()` solo acepta un token `OD`/`OS` delimitado por no-letras; valor
  ambiguo o ausente → vacío (no se asume). `--laterality R|L` explícito manda sobre el nombre, y `ImageComments` registra
  la procedencia («indicada explícitamente» o «deducida del nombre del archivo, sin verificar»).
- **Descartado:** se sospechó que `BOCT` usa `filepath.stem` como `PatientID`; con el archivo real el DICOM sale con
  `PatientID` vacío y `PatientName` vacío, así que no hay fuga por esa vía.

## 3. `np.fromstring` en modo binario: el lector FDA falla con NumPy ≥ 2.3

- **Dónde:** `oct_converter/readers/fda.py` (líneas 47, 52, 147 y 162).
- **Evidencia (ejecutada, NumPy 2.4.4, `.fda` real):** `ValueError: The binary mode of fromstring is removed, use frombuffer instead`.
- **Afecta a:** `topcon_fda_extractor.extract_from_fda` (extractor legado). No a los adapters (el lector vendorizado ya usa
  `struct`). Para comparar con `oct_converter` hace falta un entorno con NumPy < 2.3.

## 4. Segmentación FDA: nombres, ausencia de datos y un caso límite

- **Nombres:** `read_segmentation` solo traduce `MULTILAYERS_n`; los contornos `RETINA_n` del 3D OCT-1000 Mark II quedan
  con su nombre crudo (ver `topcon_3doct1000mk2_contours.md`). No es un error, pero la librería no los reconoce.
- **Ausencia de segmentación (por lectura del código, no ejecutado):** `read_oct_volume` aplica `height − v` a todos los
  valores; un valor crudo `0` (segmentación ausente) pasa a ser `height`, y deja de poder detectarse como cero. El lector
  vendorizado marca `NaN` **antes** de convertir. El extractor legado conserva esa comprobación tardía (código propio,
  no se modificó).
- **Un solo `@CONTOUR_INFO` (por lectura del código, no ejecutado):** `self.chunk_dict[b"@CONTOUR_INFO"][0]` es un entero
  cuando hay un único chunk, y se itera como lista.

## Borradores de reporte upstream (NO enviados)

1. *BOCT DICOM export writes placeholder PixelSpacing/SliceThickness* — `boct_meta.py:64-65`; propuesta: omitir o exigir
   calibración, y avisar en `ImageComments`.
2. *Laterality from filename matches OD/OS inside words* — `boct.py:139-155`; propuesta: límites de token y devolver vacío si es
   ambiguo.
3. *FDA reader fails on NumPy ≥ 2.3 (`np.fromstring` binary mode)* — `fda.py:47,52,147,162`; propuesta: `np.frombuffer`/`struct`.
