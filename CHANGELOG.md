# Changelog

All notable changes to Transducin are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Fixed
- Spectralis: el centinela `0xFFFF` (sin dato) de los B-scan se muestra en negro, no como franja blanca en volúmenes inclinados

### Added
- Revo: el DICOM OCT incluye el bloque de capas (ADR-028) con los contornos de segmentación del equipo (ILM, NFL, GCL, IPL, INL, OPL, ONL, ELM, EZOS, ISOS, BM); solo si los cortes coinciden con los B-scan y las filas caben en la imagen. Buscador de SLO y mapa de grosor de Revo siguen pendientes de validar la geometría
- Buscador de SLO: `thickness_gray_png_b64` (grosor en grises, LA) para que el visor aplique escalas de color y calcule mapas de cambio; se conserva el PNG coloreado por compatibilidad
- Spectralis: contornos de capa (`transducin/layer_contours.py`) y buscador de SLO con mapa de grosor en color (`transducin/slo_finder.py`) dentro del DICOM de imagen (bloque privado, ADR-028 propuesto); un SR por ojo (`extract_all`); tipo de estudio por volumen (nervio óptico / macular) por extensión angular
- `SpectralisE2EAdapter.to_dicom`: un DICOM OphthalmicTomographyImage por volumen B-scan del `.e2e` (mismo StudyInstanceUID; el primero coincide con la lateralidad del SR). No escribe PixelSpacing/SliceThickness (posX/posY vienen en grados, no en mm) ni el nombre del archivo; la extensión angular queda en ImageComments. Tests: `tests/test_spectralis_to_dicom.py`
- ADR-027 (propuesto): lectura de píxeles de Spectralis `.e2e` solo en local. `E2EReader.read_bscans()` decodifica los cortes B-scan (flotante propio de 16 bits + transformada logarítmica, verificado valor por valor contra una referencia) y el fondo de ojo; `BScanVolume` en `readers/_layers.py`. `scripts/e2e_contour_viewer.py`: HTML local con cortes y contornos de capa para comprobar a simple vista el mapeo de IDs; se niega a escribir dentro del repo. Tests sintéticos: `tests/test_e2e_pixels.py`, `tests/test_e2e_contour_viewer.py`
- Identidad por alias (ADR-026, fase 1): `OCTClinicalData.device_patient_id` y propiedades `patient_id` / `patient_id_source` (`noel` | `device` | `filename` | `none`); `noel_id` queda como alias opcional
- `E2EReader.read_patient_id()` y `FDAReader.read_patient_id()`: leen solo el campo del identificador interno del archivo (sin nombre ni fecha de nacimiento)
- `opt_to_dicom()`, `build_dicom_oct()` y `build_dicom_enface()` aceptan `patient_id` opcional
- Tests: `tests/test_patient_identity.py`
- `E2EReader.read_thickness_map()` y `FDAReader.read_thickness_map()` ya calculan CMT, ETDRS, mRNFL y mGCIPL (y pRNFL en circle scans) desde la segmentación embebida; los adapters Spectralis (D1) y Topcon (D2) dejan de devolver `None` (ADR-022, enmienda)
- `read_layer_volumes()` en ambos readers; `transducin/readers/_layers.py` (`LayerVolume`, `ThicknessMapData`)
- `measure_e2e_volume()` / `measure_fda_volume()`: lógica de medición compartida entre extractores y readers
- Tests con archivos sintéticos: `tests/test_readers_layers.py`, `tests/_synthetic.py`

### Changed
- `build_sr()` ya no exige `noel_id`: `PatientID = patient_id` (puede ir vacío); fecha de nacimiento y respaldo de `PatientName` solo desde datos de origen o un NOEL válido
- Adapters Spectralis/Topcon y sus extractores conservan el identificador interno del equipo aunque no sea NOEL
- Sin cambios en los watchers de producción

### Fixes
- Spectralis `.e2e`: `posX/posY` están en grados (0.3 mm/°, según el reporte de Heidelberg), no en mm; los sectores ETDRS se orientan por la posición de cada corte en el SLO (superior = y menor) en vez de suponer corte 0 = superior y la convención de Revo. Con el ojo derecho de 25 cortes los 8 sectores coinciden con el reporte de Spectralis a ~4 µm de promedio; el centro queda a 9 µm (el técnico centra la rejilla a mano). Los metadatos de corte se ordenan por índice real
- SR: fabricante y modelo según el equipo (Spectralis/Topcon ya no salen como Revo FC130)
- Se elimina la dependencia de `oct_converter` en Spectralis y Topcon: se retiran los extractores antiguos `extract_from_e2e` / `extract_from_fda` (no los usaba ningún adaptador y leían nombre y fecha de nacimiento del paciente) y su prueba de paridad. `boct_converter` (Bioptigen) conserva `oct_converter` como dependencia opcional con import diferido
- Hot folder genérico (Spectralis/Topcon): el SR y el nombre estandarizado usan el PatientID efectivo (alias NOEL o identificador del equipo, ADR-026) en vez de exigir NOEL ID; `.e2e` produce DICOM de imagen + SR en el mismo estudio
- Spectralis `.e2e`: el mapeo de IDs de contorno (BM = contorno 9) no coincidía con archivos reales de un Spectralis, que traen los contornos 0–6, 8, 14–16 y el más profundo es el 1; con ese mapa `read_thickness_map()` devolvía 0 mapas. `measure_e2e_volume` ahora elige entre dos mapas (`oct_converter_contiguous`, histórico, y `heyex_ids`: ILM=0, BM=1, RNFL/GCL=2, IPL/INL=4) validando el orden geométrico de las capas, y no mide si ninguno es consistente. Las mediciones con `heyex_ids` quedan como `extraction_confidence="assumed"` y con nota: es una hipótesis respaldada por geometría y por coincidencia entre archivo individual y consolidado, no por documentación de Heidelberg; falta compararla con el valor del reporte del equipo
- `FDAReader` fallaba con numpy ≥ 2.3 (`np.fromstring` binario eliminado)
- Capas FDA ausentes (valor crudo 0) ahora son NaN y no "altura de imagen"
- Adapters D1/D2: sin lateralidad conocida no se miden sectores (antes el extractor asumía ojo derecho)
- Watcher `.opt`: sin NOEL ID el DICOM ya no lleva `PatientID="UNKNOWN"` sino `""`
- Watcher `.opt`: ahora pasa `study_type`/`study_time` a `opt_to_dicom()`; antes `ProtocolName (0018,1030)` salía vacío y Gass no podía clasificar el scan
- Tests de regresión en `tests/test_watcher_dicom.py`
- `docker-compose.yml`: la imagen `orthancteam/orthanc:24.6.0` no existe (ahora `24.6.3`) y el healthcheck usaba `curl`, ausente en la imagen (ahora bash + `/dev/tcp`)
- CI: `ci.yml` instala `construct`; `ruff check` y `ruff format --check` pasan (se ignoran `BLE001`/`S110`/`S112` por degradación elegante deliberada)

### Added (modelo de evidencia de lateralidad, solo observación)
- `transducin/laterality_evidence.py`: fuentes agrupadas por **origen del dato** (DICOM 0020,0060/0062/9072 y privados de Cirrus = un solo origen; metadato nativo; geometría de adquisición, p. ej. signo de OCTPARAMS tag 23 en Revo; nombre de archivo = fuente débil; confirmación médica y localizador sin productor aún). Estados: declarada / inferida / confirmada / conflicto / desconocida. Un conflicto no se rotula como ojo equivocado. No cambia lateralidad ni emisiones
- Log sombra v2: evidencia por emisión, categorías de R1 (`defaulted`, `conflict`, `concordant_multi_source`, `unverified_single_source`, `inferred_only`, `no_evidence_available`, `confirmed`), interruptor inmediato (`TRANSDUCIN_SHADOW_DISABLE` o archivo `DISABLE` en el directorio del log), informe Markdown (`scripts/shadow_report.py --md`)

### Added (registro en modo sombra R1–R6, desactivado por defecto)
- `transducin/shadow_log.py`: registra por emisión (SR y webhook a Graefe) banderas de riesgo R1–R6, ruta, fuente de lateralidad, origen del centro y procedencia, **sin cambiar ninguna medición ni emisión**. Local, fuera de Git, sin PHI (HMAC-SHA256 con clave 0600), retención 30 días configurable, nunca lanza. Se activa con `TRANSDUCIN_SHADOW_LOG_DIR` y una clave HMAC
- `scripts/shadow_report.py` (`structural` / `corpus` / `log`): frecuencias agregadas sin identificadores; `validation/informe_modo_sombra_2026-11-04.md`
- Enganches de solo lectura en `build_sr` y `_notify_graefe`; 21 pruebas (SR y payload idénticos con el log activado y desactivado)

### Added (ETDRS v2 y segundo cálculo experimental, sin conectar a salidas clínicas)
- `transducin/etdrs.py`: ETDRS v2 con 9 regiones disjuntas (cuadrantes de 90°), µm reales por eje, orientación explícita (`Orientation`, con estado `validated`), centro con origen (`geometric` / `foveal_candidate_min_thickness` / `foveal_registered`) y cobertura por región; no se llama CMT sin centro foveal registrado y validado. La ruta legacy (`compute_etdrs`) y Revo/Cirrus no cambian
- Topcon experimental: segundo mapa ILM→RPE candidato (`RETINA_1→RETINA_3`) y comparación con ILM→BM; mapeo versionado v0.2.0 con la evidencia de TrueMap 2.12 / 2009 y Chen 2019; `RETINA_3` = posible límite RPE / OS-RPE
- `scripts/compare_etdrs_legacy_v2.py` y `validation/informe_comparativo_2026-11-04.md`
- Hallazgos sin corregir (solo documentados): sectores solapados en `compute_etdrs` (Revo); semi-cuña nasal (51 %) en `cirrus_extractor._etdrs_from_map`

### Added (Topcon 3D OCT-1000 Mark II, experimental)
- Espesor ILM→BM **experimental** (`transducin/topcon_experimental.py`, `scripts/topcon_experimental_thickness.py --experimental`, `TopconFdaAdapter(experimental=True).experimental_thickness()`): solo 3DOCT-1000MK2, usa `RETINA_1`/`RETINA_4` sin modificarlos, verifica la escala axial (3,5 µm/px), marca contornos ausentes y geometría inválida, `measurement_provenance = vendor_contour_derived`; **no** alimenta `cmt_um`, SR, DICOM cuantitativo ni Graefe
- Mapeo `RETINA_1..4 → ILM / IS/OS / OS/RPE / BM` como **hipótesis versionada e inmutable** (`transducin/topcon_retina_map.py`), no usada por el lector ni el adapter de producción
- Visor interactivo de contornos (`scripts/topcon_contour_viewer.py`): HTML autocontenido; etiquetas nativas y anotaciones manuales separadas; exportación JSON
- Política de cobertura ETDRS por adaptador (`transducin/etdrs_policy.py`): Revo/Cirrus sin cambios; Spectralis y Topcon con `STRICT`
- `boct_converter`: lateralidad solo con token OD/OS aislado o explícita (`--laterality`), calibración explícita (`--pixel-spacing-mm`, `--slice-thickness-mm`) y aviso en `ImageComments` si no hay calibración
- `FDAReader.read_device_info()`; `ThicknessMapData.notes` (avisos de cálculo)
- Documentación en `validation/`: contornos Topcon, contraste con Heidelberg (eyepydata), problemas de OCT-Converter, política ETDRS

### Added (deploy)
- `deploy/start_transducin.sh` y `deploy/org.oftalmos.transducin.plist`: LaunchAgent de la API (puerto 8004) en lumen

---

## [1.1.0] — 2026-11-02

### Fixes
- **Versión sincronizada**: `pyproject.toml`, `transducin/__init__.py` y `transducin/api.py` ahora reflejan el mismo número de versión; `[tool.bumpver.file_patterns]` actualizado para rastrear los tres archivos

### Validation corpus
- Añadidos resultados de corpus completo: `validation/full_corpus_results.csv` (351 archivos, 5 grupos dispositivo/site), `validation/full_corpus_stats.txt`, `validation/pipeline_stats.txt`
- Script de validación: `validation/run_full_batch.py`

### Diagnóstico CMT gap
- Investigación del 25% de maculares sin CMT: todos corresponden a FC130 SOCT 21.1.2 con BOTTOM layer en ceros; no es un error del pipeline sino una limitación del segmentador open-source en patología estructural avanzada (drusen, fibrosis sub-RPE)

---

## [1.0.0] — 2026-04-27

Primera versión pública. Pipeline completo para:

- **Optopol Revo FC130**: parser `.OPT` con especificación del formato chunk-container documentada por primera vez; B-scans → `OphthalmicTomographyImageStorage`; imágenes SLO/ENFACE/ANGPRV/OCTA_MIP → `OphthalmicPhotography8Bit`; extracción clínica CMT, ETDRS 9 sectores, pRNFL (anillo peripapillar), mRNFL, mGCIPL, biometría (AL, CCT, K1/K2), C/D ratio; SR TID 1500/1501 con SNOMED-CT y UCUM
- **Zeiss Cirrus HD-OCT**: extracción de CMT, ETDRS, RNFL, GCL/IPL desde tags privados coding scheme `99CZM` en `.EX.DCM`; SR TID 1500 vendor-aware; pipeline OCR para PDFs Cirrus (pdfplumber + pdf2image, MIT)
- **PTS 925Wi Optopol**: DICOM Secondary Capture (SOP OPV `1.2.840.10008.5.1.4.1.1.80`) con PDF embebido; `pts925_watcher` como servicio Orthanc-polling
- **Lateralidad**: inferida del signo aritmético de OCTPARAMS tag 23 (posición foveal horizontal en mm); validado al 100% en 18 archivos de dos modelos y tres versiones de software (11.5.0–21.1.2)
- **DMARKERS**: discriminador determinístico optic_nerve vs macular; override sobre inferencia por dimensiones
- **AnatomicRegionSequence**: T-AA700 (segmento anterior), T-AA610 (segmento posterior), T-AA630 (nervio óptico) en todas las instancias de imagen y SR per DICOM CP-1676
- **PixelSpacing calibrado**: derivado de PARAMS.DAT (µm/px real por scan)
- **Fechas de adquisición**: 8 tags DICOM (`AcquisitionDate/Time`, `ContentDate/Time`, `StudyDate/Time`, `SeriesDate/Time`) poblados desde UID del scan en PARAMS.DAT
- **PatientID**: vacío (`""`) cuando no hay NOEL ID — nunca `"UNKNOWN"` en producción
- **Protocolo NOEL**: `apellido_paterno[:2] + apellido_materno[0] + nombre[0] + YYYYMMDD` como PatientID universal
- **C-STORE → Orthanc PACS** vía pynetdicom; hot-folder watcher multivendor con `watchdog`
- **Licencia Apache 2.0**: PyMuPDF (AGPL-3.0) reemplazado por pdfplumber + pdf2image (MIT)
- **JOSS**: `paper.md` + `paper.bib` en raíz del repo
- **Tests**: 22 pytest cubriendo magic bytes, CMT, ETDRS, BM/BOTTOM fallback, NOEL ID, OCTPARAMS tag 23, tipos de scan

[1.0.0]: https://github.com/oftalmos-org/transducin/releases/tag/v1.0.0
