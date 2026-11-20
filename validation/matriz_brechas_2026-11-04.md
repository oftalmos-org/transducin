# Matriz de brechas, trazabilidad y mitigación — Transducin (2026-11-04)

Complementa `auditoria_capacidades_2026-11-04.md`. **Ningún cambio de código ni de resultados clínicos** acompaña a este documento.
Etiquetas de certeza en la trazabilidad: **[V]** verificado leyendo el código citado · **[I]** inferido, requiere comprobación con datos o prueba.

## 1. Riesgos actuales de REVO y Cirrus — prioridad ALTA

| # | Riesgo | Rutas afectadas | Qué llega realmente a Graefe | Certeza |
|---|---|---|---|---|
| R1 | Lateralidad por defecto "R"/"L" cuando no está verificada | SR: `sr_builder.py:236,334,424` (`data.laterality or "R"`); Revo disco: `opt_extractor.py:733` (`or "R"`); Revo `compute_etdrs(..., laterality="L")` por defecto (`revo_opt_reader.py:731,764,798`); lectores legacy con "R" por defecto | `laterality` tal como lo tenga `clinical_data` (puede ser vacío); el SR sí fija "R" | [V] el default; [I] cuántos estudios reales lo activaron |
| R2 | Sectores ETDRS solapados (Revo, y por herencia Spectralis/Topcon vía `compute_etdrs_with_policy`) | `revo_opt_reader.compute_etdrs` → SR (sectores) → Gass (panel ETDRS) | **Nada**: el webhook solo envía `cmt_um` (disco central, no solapado) | [V] solapamiento (informe comparativo); [V] el webhook no envía sectores |
| R3 | Cuña nasal incompleta en Cirrus (≈51 % de los 90°, corte de `arctan2`) | `cirrus_extractor._etdrs_from_map` → SR (sectores N interior/exterior) → Gass | Nada (solo `cmt_um`) | [V] |
| R4 | Posible inversión nasal/temporal | Cirrus: N a la izquierda de la imagen para ambos ojos (`_etdrs_from_map`) y el supuesto "lateralidad se aplica en el SR builder" no existe; Revo: OD temporal A>0 / OS A<0; v2 `LEGACY_ASSUMED` es otra convención | Nada directo; sí rótulos S/N/I/T del SR y de Gass | [V] convenciones incompatibles; [I] cuál (si alguna) es correcta |
| R5 | Centro geométrico tratado como fóvea | Revo, Spectralis, Topcon: centro del volumen; SR etiqueta `Foveal retinal thickness` / "CMT" / sitio fóvea (`sr_builder.py:83,136,243-246`); webhook envía `cmt_um` como CMT | **`cmt_um` del centro geométrico** (D1/D2 y Revo) | [V] |
| R6 | Medición publicada sin procedencia ni validación | `measurement_provenance` no está en SR ni en webhook; Revo/Cirrus nunca la fijan; `extraction_confidence="confirmed"` en D1/D2 aun siendo derivado; webhook no consulta validación | `cmt_um` sin ningún indicador de método, centro o estado | [V] |

Cirrus tiene una ventaja que el resto no: lee la posición de fóvea del fabricante (`0073,1140`), así que R5 le afecta menos (salvo el respaldo al centro de imagen cuando falta el tag, [V] `cirrus_extractor.py:174-192`).

## 2. Matriz de trazabilidad: origen → extracción → cálculo → DICOM/SR → Graefe

| Ruta | Origen | Extracción | Cálculo | Centro | DICOM/SR | Graefe (webhook) | Defectos activos |
|---|---|---|---|---|---|---|---|
| **Revo `.opt`** | Archivo OCT propietario (TOP, BM, NFL) | `opt_extractor` + `revo_opt_reader` | Reconstruido: CMT = media (BM−TOP) en 1 mm; ETDRS legacy | Centro del volumen (`b0=n_b//2`) | DICOM OPT (píxeles) + SR TID 1500 con "CMT", sectores | `cmt_um`, `laterality`, `rnfl_global_um` | R1, R2, R4, R5, R6; ~25 % sin CMT |
| **Cirrus DICOM** | Tags privados ILM/BM (`0073,1150/1155`) + `FoveaPosition` | `cirrus_extractor` | Reconstruido: espesor ILM→BM y `_etdrs_from_map`; fabricante no entrega CMT en este tag [I] | Fóvea del fabricante; respaldo = centro | DICOM original pasa intacto + SR | `cmt_um`, `laterality` | R3, R4, R6 (R1 solo vía SR) |
| **Cirrus PDF** | Reporte impreso | OCR | **Original del fabricante** (valores leídos) | "Fovea" del reporte | SR desde datos OCR | `cmt_um` | Lateralidad por nombre de archivo; `measurement_provenance` vacía |
| **Spectralis `.e2e` (D1)** | Contornos del fabricante (índices fijos) | `e2e_reader` | Reconstruido (`vendor_contour_derived`) + ETDRS legacy con política | Geométrico | **Solo SR** (`to_dicom()=[]`) | `cmt_um` | R2, R4, R5, R6; identidad de capas sin fuente |
| **Topcon FDA DRI/Triton (D2)** | `@CONTOUR_INFO` `MULTILAYERS_n` | `fda_reader` | Reconstruido (`vendor_contour_derived`) | Geométrico | **Solo SR** | `cmt_um` | R2, R4, R5, R6; sin datos Triton reales |
| **Topcon MK2** | `RETINA_1..4` | `fda_reader` (sin identificar) | `cmt_um=None`; experimental solo con `experimental=True` | Geométrico | Ninguno | Ninguno | Ninguno activo (correctamente excluido) |

Cobertura de la afirmación "sin cambios silenciosos": la suite actual no contiene una prueba que compare la salida legacy antes y después de un cambio. Eso es parte de la mitigación (§3).

## 3. Estrategia de mitigación (propuesta; no modifica rutas actuales)

Principio: **no corregir silenciosamente ni seguir emitiendo silenciosamente.** Tres capas, en este orden, cada una reversible:

1. **Observabilidad sin cambio de comportamiento (modo sombra).** En cada emisión (SR, webhook), registrar en un log local `emission_audit.jsonl` los indicadores de riesgo: `laterality_source`, `laterality_defaulted`, `center_origin`, `etdrs_impl` (legacy/v2), `orientation_validated`, `provenance`. Sin cambiar ningún valor. Permite medir cuántos estudios reales están afectados por R1–R6 y alimenta la decisión de migración.
2. **Anotación aditiva.** Añadir campos al webhook y al SR (nunca renombrar ni quitar): `measurement_provenance`, `center_origin`, `orientation_status`, `laterality_status`, `etdrs_impl`, `method_version`. Graefe puede ignorarlos hasta tener columnas. Los consumidores antiguos no se rompen.
3. **Compuerta de salida (fail-closed) para lo nuevo, decisión explícita para lo antiguo.** Una política declarativa `(adaptador, campo) → permitido | anotado | bloqueado`. Todo campo o adaptador nuevo nace *bloqueado*. Para lo existente, el paso a "bloqueado" (p. ej. SR con lateralidad desconocida en lugar de "R") es un **cambio clínico** y requiere tu aprobación estudio por estudio de política, con una nota de versión y una ventana de reproceso; hasta entonces permanece como está, pero visible en el log de la capa 1.

Prueba de regresión previa a cualquier migración: *golden outputs* (hash de SR + JSON) de los casos sintéticos y del corpus público para detectar cualquier cambio no declarado.

## 4. Diseño de inmutabilidad y procedencia (el SHA-256 no basta)

Un hash solo prueba que unos bytes no cambiaron; no dice dónde están, de quién son, ni cómo se derivó nada. Se propone (ADR-024 propuesto) un **registro de originales** con seis piezas:

| Pieza | Contenido |
|---|---|
| **Archivo original preservado** | Copia de solo lectura, direccionable por contenido (`originals/<sha256[:2]>/<sha256>/<nombre original>`), nunca modificada ni normalizada en sitio; réplica en Vitreum con credenciales distintas |
| **Identificador y ubicación persistentes** | `original_id` estable (`urn:retinaos:orig:sha256:<hash>`), lista de ubicaciones (ruta en Vitreum, ID de Orthanc, SOP Instance UID), independiente del nombre de archivo |
| **Hash verificable** | SHA-256 + tamaño, más un segundo algoritmo (BLAKE3 o SHA-3) y fecha de la última verificación de integridad |
| **Fecha/hora originales de adquisición** | Tal cual la declaró el equipo (con indicación de zona horaria y de si se infirió); campo separado de `ingested_at` de Transducin y de la fecha de registro en Graefe. Nunca se sobrescribe |
| **Metadatos y segmentaciones originales** | Volcado fiel de metadatos (sidecar `source.json`, sin interpretar) y contornos del fabricante (`contours_original.npz`) con el nombre de capa tal como lo da el equipo, aparte de la interpretación (`RETINA_3` sigue siendo `RETINA_3`; "posible RPE" vive en una tabla de mapeo versionada) |
| **Historial y derivados** | Cada transformación es un `DerivedRecord`: entradas (`original_id` o derivado previo), método, parámetros, versión de Transducin + commit, fecha de proceso, responsable (persona o proceso), hash de la salida y estado de validación. Los derivados forman un grafo dirigido; el original es la raíz y nunca se reemplaza. En DICOM: `ReferencedImageSequence`/`SourceImageSequence` + `DerivationCodeSequence`, y `OriginalAttributesSequence` (0400,0561) si se modifica un atributo |

**Política de conservación, integridad y acceso**
- *Conservación:* originales y su registro, indefinidos mientras exista el expediente (NOM-004 fija mínimos clínicos; confirmar el plazo con el responsable de datos); derivados regenerables pueden purgarse, su `DerivedRecord` no.
- *Integridad:* permisos de solo lectura / WORM en el almacén, verificación periódica (fixity) con alerta por discrepancia, registro de manifiestos encadenado por hash (cada entrada incluye el hash de la anterior), respaldo con prueba de restauración.
- *Acceso:* roles RBAC de Graefe; consentimiento vía Arrestin/JAU para datos de pacientes; procesamiento solo local (LM Studio/Ollama en `lumen`/`Photon`); ningún original sale a servicios externos. Las correcciones del médico y los resultados de Müller son derivados firmados con identidad de usuario, nunca ediciones del original.

Datasets de acceso controlado (p. ej. AI-READI) se guardan en un almacén **separado**, con su licencia y sin pasar por el corpus redistribuible `public/` (ver especificación D).

## 5. Matriz de brechas (estado → cierre mínimo)

| Área | Estado hoy (implementación / evidencia / validación clínica) | Brecha | Primer cierre mínimo (sin cambiar resultados) |
|---|---|---|---|
| Reconocimiento de estudio | parcial por extensión / sintética / no evaluada | Sin contenido, SOP, modelo ni tipo | `identify_study()` solo lectura, modo sombra, registra discrepancias con el ruteo actual |
| Originales inmutables | parcial / — / no evaluada | Sin registro, sin contornos persistidos, D1/D2 sin imagen | Esquema de `SourceRecord`/`DerivedRecord` (ADR-024) revisado antes de codificar |
| Lateralidad | parcial / sintética / no evaluada | Sin cotejo; defaults | `LateralityEvidence`: lista de fuentes, estado `verified`/`conflict`/`unverified`; solo registra |
| Orientación | parcial / ninguna / no evaluada | Tres convenciones, ninguna verificada | Protocolo de verificación con SLO/B-scan localizador en estudios propios (spec D) |
| Segmentación nativa | implementado / real-pequeña / no evaluada | No persistida; identidades sin fuente | Exportar contornos originales con nombre nativo + confianza |
| Centro foveal | parcial / — / no evaluada | Origen no propagado | `center_origin` en JSON/SR (aditivo) |
| CST 1 mm | parcial (solo v2) / sintética / no evaluada | Sin nombre, definición ni validación en producción | ADR-025: `central_subfield_thickness_um` ≠ `center_point_thickness_um`, con superficies, origen, cobertura, algoritmo, versión y estado |
| ETDRS v2 | implementado, no conectado / sintética + real-pequeña / experimental | Sin trazado completo ni migración | Auditar con el *log* de sombra qué estudios reales difieren y por cuánto |
| Visor y µm | parcial / casi nula / no evaluada | Sin prueba de longitud, sin capas, sin geometría explícita | Prueba con espaciado sintético; luego overlay de contornos originales |
| Height Map Segmentation | ausente | Sin lector/escritor | Spike de lectura con datos AI-READI **cuando se aprueben**; antes, con objetos sintéticos |
| Entrega a Graefe | parcial / sintética / no evaluada | Sin fecha original, procedencia, método; ordena por fecha de registro | Esquema v2 aditivo (ADR) + columna `acquisition_datetime` en Graefe |
