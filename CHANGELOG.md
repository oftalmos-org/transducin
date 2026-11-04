# Changelog

All notable changes to Transducin are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- `E2EReader.read_thickness_map()` y `FDAReader.read_thickness_map()` ya calculan CMT, ETDRS, mRNFL y mGCIPL (y pRNFL en circle scans) desde la segmentación embebida; los adapters Spectralis (D1) y Topcon (D2) dejan de devolver `None` (ADR-022, enmienda)
- `read_layer_volumes()` en ambos readers; `transducin/readers/_layers.py` (`LayerVolume`, `ThicknessMapData`)
- `measure_e2e_volume()` / `measure_fda_volume()`: lógica de medición compartida entre extractores y readers
- Tests con archivos sintéticos: `tests/test_readers_layers.py`, `tests/_synthetic.py`

### Fixes
- `FDAReader` fallaba con numpy ≥ 2.3 (`np.fromstring` binario eliminado)
- Capas FDA ausentes (valor crudo 0) ahora son NaN y no "altura de imagen"
- Adapters D1/D2: sin lateralidad conocida no se miden sectores (antes el extractor asumía ojo derecho)
- Watcher `.opt`: sin NOEL ID el DICOM ya no lleva `PatientID="UNKNOWN"` sino `""`
- Watcher `.opt`: ahora pasa `study_type`/`study_time` a `opt_to_dicom()`; antes `ProtocolName (0018,1030)` salía vacío y Gass no podía clasificar el scan
- Tests de regresión en `tests/test_watcher_dicom.py`
- `docker-compose.yml`: la imagen `orthancteam/orthanc:24.6.0` no existe (ahora `24.6.3`) y el healthcheck usaba `curl`, ausente en la imagen (ahora bash + `/dev/tcp`)
- CI: `ci.yml` instala `construct`; `ruff check` y `ruff format --check` pasan (se ignoran `BLE001`/`S110`/`S112` por degradación elegante deliberada)

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
