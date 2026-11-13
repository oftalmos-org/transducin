# Modo sombra R1–R6: implementación, resultados sobre el corpus público y criterios de bloqueo (2026-11-04)

Estado: el registro está implementado y probado; **ninguna medición, SR ni payload cambió** (hay pruebas que lo comprueban). Las frecuencias
reales de producción **todavía no existen**: el corpus público casi no contiene rutas expuestas (§3). Se obtienen activando el log en los nodos
(§1) o ejecutando el analizador localmente sobre copias privadas (§4).

## 1. Qué se implementó

| Pieza | Archivo |
|---|---|
| Módulo de registro y evaluación R1–R6 | `transducin/shadow_log.py` |
| Enganches de solo lectura (2 líneas cada uno) | `sr_builder.build_sr` (emisión `sr`), `hot_folder_watcher._notify_graefe` (emisión `webhook`) |
| Informe agregado (estructural / corpus / log real) | `scripts/shadow_report.py` |
| Pruebas | `tests/test_shadow_log.py` (21, todas sintéticas) |

Condiciones acordadas y cómo se cumplen:

| Condición | Cumplimiento |
|---|---|
| Local, fuera de Git | Se rechaza un directorio dentro de un árbol Git (probado) |
| Sin PHI ni payloads clínicos | No se guarda `noel_id`, nombres, fechas, nombres de archivo, UIDs ni valores medidos; prueba de búsqueda de cadenas |
| HMAC-SHA256 con clave protegida | `study_hmac` y `patient_hmac` (32 hex); clave ≥16 bytes, archivo de clave con permisos 0600 o el log se desactiva |
| R1–R6, ruta, fuente de lateralidad, origen del centro, algoritmo, procedencia | Un registro por emisión (`flags`, `route`, `laterality_source`, `center_origin`, `etdrs_impl`, `value_origin`, `provenance_internal`) |
| Retención configurable, 30 días por defecto | `TRANSDUCIN_SHADOW_RETENTION_DAYS`; purga diaria (probada) |
| No modifica mediciones ni emisiones | Comparación SR con log activado/desactivado sobre 1 341 nodos (solo difieren 19 UIDs/fechas generados en cada ejecución) y payload del webhook byte a byte |
| Sin servicios externos | Sin red |
| Desactivado por defecto; nunca lanza | Probado (ruta inválida, objeto inválido) |

**Activación en un nodo (HP Z2 / lumen)** — fuera del repositorio:

```bash
umask 077
mkdir -p ~/transducin-shadow
openssl rand -hex 32 > ~/transducin-shadow/hmac.key
export TRANSDUCIN_SHADOW_LOG_DIR=~/transducin-shadow/logs
export TRANSDUCIN_SHADOW_HMAC_KEY_FILE=~/transducin-shadow/hmac.key
export TRANSDUCIN_SHADOW_RETENTION_DAYS=30
```

(en el servicio NSSM o el LaunchAgent, como variables de entorno del proceso del watcher). Después de unos días:
`PYTHONPATH=. python scripts/shadow_report.py log ~/transducin-shadow/logs` imprime solo cuentas.

Limitación honesta: la clave no debe viajar a otro nodo; sin ella los HMAC no se pueden correlacionar. Si cambia la clave, la correlación entre
periodos se rompe (aceptable para este uso).

## 2. Exposición estructural por ruta (determinista, 100 % de las emisiones con CMT, ETDRS y lateralidad presente)

Fuente: `validation/shadow_structural_2026-11-04.json`. Es lo que ocurre **por construcción** del código, no una frecuencia observada.

| Ruta | Origen del valor | R1 lateralidad | R2 sectores solapados | R3 cuña nasal | R4 orientación sin validar | R5 centro geométrico como foveal | R6 sin procedencia/validación |
|---|---|---|---|---|---|---|---|
| Revo `.opt` | reconstruido | no cotejada | **sí** | no | **sí** | **sí** | sí |
| Cirrus DICOM | reconstruido | no cotejada | no | **sí** | **sí** | posible (fóvea del fabricante o centro de imagen, no registrado) | sí |
| Cirrus PDF | original del fabricante (OCR) | no cotejada | no | no | **sí** | no | sí |
| Spectralis `.e2e` | reconstruido | no cotejada | **sí** | no | **sí** | **sí** | sí |
| Topcon FDA | reconstruido | no cotejada | **sí** | no | **sí** | **sí** | sí |

Si la lateralidad llega vacía, R1 pasa a `defaulted`: el SR asigna "R" (`sr_builder.py:236,334,424`) y el webhook envía `""`.

Distinción pedida (valor original frente a reconstruido): solo Cirrus PDF entrega valores originales del fabricante; **todas las demás
rutas reconstruyen el valor** a partir de contornos del fabricante.

## 3. Análisis del corpus público

Fuente: `validation/shadow_corpus_publico_2026-11-04.json` (cuentas agregadas; sin nombres de archivo). Se pasó cada archivo por su adaptador, sin generar SR ni webhook.

| Resultado | Archivos |
|---|---|
| Procesados por un adaptador | **4** (todos Topcon 3D OCT-1000 MK2, incluidas copias en `_duplicates`) |
| Sin adaptador (extensión no soportada: imágenes `png`/`tif`, `.vol`, `.OCT` Bioptigen, JSON, etc.) | 291 |

Los 4 registros: ruta `topcon_fda`, lateralidad desde metadatos nativos, **sin mediciones** (los contornos `RETINA_1..4` no están identificados, por diseño),
por lo que R2, R3, R4, R5 y R6 son 0/4 y R1 = `unverified_single_source` en 4/4.

**Conclusión:** el corpus público no permite estimar la frecuencia real de R1–R6 en Revo, Cirrus ni Spectralis; no contiene esos formatos
en un formato que los adaptadores acepten (los `.vol` de Heidelberg no están enrutados, solo `.e2e`). Declararlo así evita una cifra falsa de "0 %".
Las frecuencias reales hay que medirlas donde está la producción.

Aviso de manifiestos: los `MANIFEST.json` públicos existentes no tienen todos los campos de CLAUDE.md §2.1.1 (`license_or_access`, `format_real`,
`deidentification_status`, `validation_status` y `claude_session_ok` aparecen vacíos; el de `heidelberg/derived` está sin rellenar y el de Bioptigen
tiene la URL pendiente). Deben completarse antes de ampliar el uso del corpus.

## 4. Cómo obtener las frecuencias reales (sin enviar nada fuera de la máquina)

1. **Prospectivo:** activar el log en HP Z2 y lumen (§1). Cada estudio nuevo suma un registro por emisión.
2. **Retrospectivo local:** `PYTHONPATH=. python scripts/shadow_report.py corpus <copia_local>` sobre una copia **local** de estudios privados, ejecutado por ti; solo
   imprime cuentas. No se ejecutó aquí porque `private/` exige manifiesto con `claude_session_ok` y procesamiento local (CLAUDE.md §2.1.1, §9.4).
3. Con el log acumulado, `shadow_report.py log DIR` entrega por ruta y emisión: n, R1 (`defaulted` / `unverified_single_source`), R2–R6 y fuentes de lateralidad.

Lo que el log **no** puede medir todavía: R1 como "ojo equivocado" real (solo detecta lateralidad vacía o sin cotejo); R4 como inversión real.
Para eso hace falta una referencia independiente (SLO/localizador y reporte del fabricante; ver la especificación de adquisición).
Propuesta de ampliación del log (no implementada): registrar todas las fuentes de lateralidad disponibles por estudio y contar discrepancias.

## 5. Mitigaciones y criterios de bloqueo (propuestas; **nada activado**)

Principios: indicadores de funcionamiento visibles → anotar (aditivo) → bloquear solo con criterio y aprobación. Todo cambio detrás de un interruptor
desactivado por defecto, con SR versionado, *golden tests* previos y plan de reversión.

| Riesgo | Mitigación propuesta | Criterio para activarla | Quién decide |
|---|---|---|---|
| **R1** lateralidad | (a) Dejar de asignar "R" por defecto: SR sin medición lateralizada y marcado "lateralidad no verificada"; webhook con `laterality_status`. (b) Registrar todas las fuentes y bloquear ante **conflicto**. (c) Anotar `unverified_single_source` sin bloquear | (a) Cualquier registro `defaulted` en 30 días de log en producción, o aceptación explícita de reprocesar los previos; (b) tras implementar el cotejo, 0 discrepancias no explicadas en la validación con estudios propios | Titular |
| **R5** centro geométrico como foveal | Sin cambiar el número: etiquetar "centro geométrico del volumen" en SR y añadir `center_origin` al webhook; no llamar CMT/foveal salvo `foveal_registered` o `vendor_reported` | Basta con que el log confirme la fracción de estudios con `geometric_volume_center`; la etiqueta no cambia la medición | Titular |
| **R2** sectores solapados / **R3** cuña nasal | No publicar valores por sector de rutas legacy; mantener solo el disco central, hasta migrar a ETDRS v2 | ETDRS v2 verificado frente al reporte del fabricante en estudios propios, con límites de concordancia fijados **a priori** con el estadístico, y orientación verificada (R4) | Titular + estadístico |
| **R4** orientación | Mantener `orientation_validated = false`; sectores N/T sin publicar hasta verificar con SLO/localizador por equipo | Orientación verificada en un número de ojos por equipo fijado en el protocolo (el handoff sugiere 5–10 por equipo en fase técnica) sin discrepancias | Titular |
| **R6** procedencia/validación | Campos aditivos `measurement_provenance`, `center_origin`, `validation_status`, `method_version` en SR y webhook; compuerta por `publishable_as_clinical` | Aprobación de ADR-025 y esquema de Graefe | Titular |

Orden recomendado de activación: R6 y R5 (solo anotan) → R1(a) (bloquea una asignación por defecto) → R1(b), R2–R4 (dependen de verificación con estudios propios).
Ninguna de estas cifras es un umbral clínico validado; son criterios propuestos para fijar con el protocolo.
