# Auditoría de capacidades — Transducin, Gass, Graefe (2026-11-04)

Alcance: lectura de código y documentos Markdown. No se leyó el corpus clínico ni se imprimió ningún dato de paciente. Las pruebas citadas se
ejecutaron en esta sesión sobre el commit `89b4814` (ver §5). Tres dimensiones **independientes** (no se promedian ni se infieren entre sí):

| Dimensión | Valores |
|---|---|
| **Implementación** | `implementado` · `parcial` · `ausente` |
| **Evidencia técnica** | `sintética` · `real-pequeña` (n < 30 ojos o un solo sitio/equipo) · `real-amplia` · `independiente` (comparada con una referencia que no es el mismo código) |
| **Validación clínica** | `no evaluada` · `experimental` · `validada para un uso específico` |

Regla aplicada: **nada con solo pruebas sintéticas o una muestra real pequeña se marca como clínicamente validado.** Ninguna capacidad de esta
auditoría alcanza hoy `validada para un uso específico`; lo más alto es `experimental`.

## 1. Transducin

| Capacidad | Implementación | Evidencia técnica | Validación clínica | Notas / referencia |
|---|---|---|---|---|
| Ruteo de adaptador por extensión | implementado | sintética | no evaluada | `base_adapter.py:123-130`, `adapter_registry.py:21-41`; `test_adapter_registry.py` |
| Identificación por contenido (fabricante, modelo, SOP Class, tipo de adquisición) | **ausente** | — | no evaluada | El adaptador Cirrus acepta cualquier `.dcm` (`adapters/cirrus.py:37-39`) |
| Cirrus DICOM: ILM/BM (tags privados 0073,1150/1155) y `FoveaPosition` (0073,1140) | implementado | real-pequeña (informes previos) | no evaluada | Solo formas 128×512 y 200×200; escala axial constante |
| Cirrus ETDRS (`_etdrs_from_map`) | implementado, **defectuoso** | real-pequeña | no evaluada | Cuña nasal 51 %; N/T fijos por posición de imagen; no recibe lateralidad |
| Cirrus PDF (OCR) | implementado | real-pequeña | no evaluada | Lateralidad por nombre de archivo (`OD|OS|OU`) |
| Revo `.opt`: CMT (grosor 1 mm centrado en el volumen) | implementado | real-pequeña + **independiente** (Bland-Altman contra valor del fabricante, 8 estudios sitio B) | experimental | ~25 % de macular sin CMT (BOTTOM en cero) |
| Revo ETDRS 9 sectores (`compute_etdrs`) | implementado, **defectuoso** | sintética | no evaluada | Medios anillos solapados S/N/I/T; orientación fija; defecto "L" (`revo_opt_reader.py:731-798`) |
| Spectralis `.e2e` (D1): ILM/BM, lateralidad | implementado | sintética + real-pequeña (2 `.vol` por vía eyepydata) | no evaluada | Identidades `contour0`/`contour9` sin fuente documentada |
| Topcon FDA DRI/Triton (D2): `MULTILAYERS_1..10` | implementado | sintética; **sin datos Triton reales** | no evaluada | Cadena de vendor inconsistente (`topcon_dri_oct` / `topcon_triton`) |
| Topcon 3D OCT-1000 MK2: contornos `RETINA_1..4` conservados | implementado | real-pequeña (1 ojo izquierdo UK Biobank) | no evaluada | `unidentified_contours`, `fda_reader.py:282-291` |
| Topcon MK2: espesor ILM→BM e ILM→RPE candidato | implementado (opt-in) | real-pequeña (1 ojo) | **experimental** | `vendor_contour_derived`; no alimenta `cmt_um`/SR/Graefe |
| Mapeo `RETINA_n → capa` v0.2.0 | implementado como hipótesis | revisión visual de 1 lector + bibliografía | **experimental** | Correspondencia binaria no demostrada |
| ETDRS v2 (`etdrs.py`) | implementado, **no conectado** | sintética + real-pequeña | experimental | 9 regiones disjuntas, `Orientation.validated`, origen del centro |
| Candidato de fóvea (mínimo de espesor) | implementado | sintética | experimental | Nunca se trata como fóvea confirmada |
| Política de cobertura ETDRS (`etdrs_policy`) | implementado | sintética | no evaluada | Aplica a D1/D2 sobre el cálculo legacy |
| Lateralidad: fuentes y cotejo | **parcial** | sintética | no evaluada | Una fuente por vendor; sin detección de conflicto; dos prioridades distintas en Cirrus; `except: pass` en lectores |
| Orientación nasal/temporal | **parcial** | ninguna | no evaluada | Tres convenciones incompatibles (Revo, Cirrus, v2); ninguna verificada |
| Centro foveal registrado | **parcial** | — | no evaluada | Solo Cirrus lee posición del fabricante; resto, centro geométrico |
| Variable CST / `center_point_thickness` | **parcial** | sintética (solo v2) | no evaluada | Producción usa `cmt_um`, SNOMED 422453003 "Foveal retinal thickness", etiqueta "CMT" |
| Procedencia de la medición (`measurement_provenance`) | parcial | sintética | no evaluada | Solo D1/D2 la fijan; Revo/Cirrus la dejan vacía; **no se exporta** a SR, webhook ni DICOM |
| Preservación de originales (píxeles, contornos, medidas del fabricante) | **parcial** | — | no evaluada | Revo y Cirrus DICOM: sí en DICOM. D1/D2: `to_dicom()` devuelve `[]`; contornos solo en memoria |
| DICOM SR TID 1500 | implementado | sintética + real-pequeña | no evaluada | `sr_builder.py`; cae a "R" sin lateralidad |
| DICOM Height Map Segmentation / Segmentation | **ausente** | — | no evaluada | Solo mencionado en `docs/DICOM_standards_reference.md` |
| FHIR R4 | **ausente** | — | no evaluada | ADR-020 lo descartó para esta ruta |
| Webhook JSON a Graefe | implementado | sintética | no evaluada | Sin procedencia, orientación, centro ni versión; no condicionado a validación |
| Datos reales Spectralis nativo, SOLIX, Triton | **ausente** | — | — | Ver `spec_adquisicion_spectralis_solix_triton.md` |

## 2. Gass (repo hermano `Gass/`)

| Capacidad | Implementación | Evidencia técnica | Validación clínica | Notas |
|---|---|---|---|---|
| Medición de longitud en µm | implementado (`LengthTool` reetiquetada, `BScanViewer.tsx:267-276`) | **solo el bloqueo** `hasPhysicalSpacing` tiene prueba; ninguna prueba mide una longitud real | no evaluada | Se desactiva si el espaciado falta o es 1:1 |
| Herramienta de área | ausente | — | — | |
| Lectura de espaciado (`PixelSpacing`) | parcial | Gass depende del proveedor de Cornerstone; no lee `SharedFunctionalGroups`/`PerFrame` | no evaluada | No confirmado para multiframe mejorado |
| Geometría recibida de Transducin | parcial | simulada (fetch mockeado) | no evaluada | `/convert` devuelve metadatos de job, sin geometría |
| Apilado de B-scans multiframe | implementado | sintética (SOP de fotografía con Modality OPT) | no evaluada | No verifica SOP Class de tomografía oftálmica |
| Panel ETDRS numérico (desde SR) | implementado | sin prueba | no evaluada | No dibuja rejilla sobre la imagen; **los rótulos heredan los defectos de orientación** |
| Gráfico RNFL | implementado | sintética (geometría) | no evaluada | |
| Mapa GCL / mapas de espesor | ausente (stub) | — | — | |
| Contornos / segmentación / Height Map | ausente (el `SegmentationStore` no tiene escritores) | — | — | |
| Corrección manual y versionado | ausente | — | — | |
| Pruebas de componente del visor | ausente | — | — | |

**Conclusión para la sección 10 del handoff:** la medición en µm ya existe y no debe duplicarse. Lo que falta es (a) una prueba con espaciado
sintético conocido, (b) comprobar el espaciado en volúmenes multiframe mejorados y (c) una vía explícita de geometría desde Transducin.

## 3. Graefe (repo hermano `antiscribe/`)

| Capacidad | Implementación | Evidencia técnica | Validación clínica | Notas |
|---|---|---|---|---|
| Webhook `POST /api/transducin/new-study` | implementado | **ninguna** (no hay pruebas) | no evaluada | `routes/transducin.py:89` |
| Lectura de DICOM SR | ausente | — | — | Sin pydicom/highdicom |
| Fecha original vs fecha de registro | **parcial, con riesgo** | — | no evaluada | `captured_at = utcnow()` ordena el listado; `study_datetime` es texto libre en `raw_string` |
| Procedencia/método/versión/`vendor_reported` | ausente | — | — | |
| Columna CST / `central_subfield` / 9 regiones | ausente | — | — | Solo `cmt_od/oi` enteros manuales por visita |
| Tendencias sin mezclar metodologías | ausente | — | — | `/oct-summary` devuelve el último estudio por ojo y tipo |
| `observations` (FHIR, SNOMED, UCUM) | implementado | sin escritores desde Transducin | no evaluada | Experimental para este flujo |
| `ImagingStudy` / `DiagnosticReport` | ausente | — | — | |
| Ingesta Orthanc | ausente (el poller tiene un `TODO`, `orthanc_poller.py:129`) | — | — | |
| Vínculo PACS | parcial | — | — | Coincidencia por subcadena en `raw_string.contains(study_uid)` |

## 4. Müller

Existe un directorio `Muller/` con un `main.py` mínimo. No se auditó como capacidad: está **ausente** como segmentador y no se entrena (decisión del handoff).

## 5. Estado de pruebas (commit `89b4814`, Python 3.14.3 en `.venv314`)

| Ejecución | Resultado |
|---|---|
| Con `TRANSDUCIN_REAL_DATA_DIR=~/transducin-real-data` | **128 pasaron** |
| Sin corpus | **111 pasaron, 17 omitidas** (el informe anterior citaba 108 + 20) |
| `ruff check transducin/ tests/` y `ruff format --check` (alcance de CI) | limpio |
| `ruff check .` completo | 40 errores y 14 archivos sin formatear, **todos en scripts raíz, `validation/*.py` y `CONTRIBUTING.md` que CI no revisa**; no se tocaron |

Nota: la memoria del proyecto indica `.venv/bin/python` (Python 3.9.6), pero ese entorno ya no existe; hoy el entorno es `.venv314` (3.14.3).
