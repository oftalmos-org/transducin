# Informe — cotejo de lateralidad y modo sombra (2026-11-04)

Estado: modelo de evidencia implementado y probado con datos **sintéticos y públicos**; validado en el entorno de **lumen** con una copia aislada.
**Aún no hay emisiones reales evaluadas**: se generan en HP Z2 (ver §5). Nada de lo siguiente cambia lateralidad, mediciones, SR, webhooks ni rutas de Graefe.

## 1. Modelo de evidencia

Las fuentes se agrupan por **origen del dato**: dos campos que salen del mismo dato original cuentan como una sola prueba.

| Grupo de origen | Fuentes | Fuerza |
|---|---|---|
| `device_dicom_header` | `ImageLaterality` (0020,0062), `Laterality` (0020,0060), `FrameLaterality` (0020,9072), atributos privados de Cirrus (0063,1005; 0057,1015) | fuerte (**un solo origen**) |
| `vendor_native` | metadato nativo (E2E, FDA) tal como lo leyó el extractor | fuerte |
| `acquisition_geometry` | Revo: signo de OCTPARAMS tag 23 (independiente del rótulo del nombre) | fuerte |
| `filename` | token aislado OD/OS en el nombre de archivo | **débil** (inferencia) |
| `physician_confirmed`, `independent_acquisition` | confirmación médica; información independiente de adquisición (p. ej. localizador) | referencia (**sin productor aún**) |

Estados: **declarada** · **inferida** (solo fuente débil) · **confirmada** (médico/independiente, sin contradicción) · **conflicto** (entre grupos o dentro de un grupo)
· **desconocida**. Un conflicto **no** se rotula como ojo equivocado: señala que una fuente falla; el error real requiere una referencia independiente.
Además se compara el valor **emitido** con la evidencia (`match` / `mismatch` / `no_consensus` / `emitted_empty`).

Categorías de R1: `defaulted` (vacía; el SR emite "R"), `conflict`, `confirmed`, `concordant_multi_source` (≥2 orígenes concordantes), `unverified_single_source`,
`inferred_only`, `no_evidence_available`, `other_value`.

## 2. Resultados con datos sintéticos (47 pruebas: 25 del log y 22 del modelo de evidencia)

Cubren: normalización; tokens de nombre de archivo ambiguos; DICOM de un solo origen que no cuenta doble; desacuerdo interno de un grupo; FrameLaterality en grupos
funcionales; atributos privados de Cirrus; Revo con nombre y tag 23 concordantes, en conflicto, ausentes/cero, archivo grande y lectura fallida; contradicción médica;
`B` (ambos ojos) que no es afirmación lateral; interruptor `DISABLE`; ausencia de PHI y de nombres de archivo en el log; SR y payload idénticos con el log activo.
Suite completa: **175 pasan con el corpus; 158 pasan y 17 omitidas sin él** (ver cierre).

## 3. Corpus público (`validation/shadow_corpus_publico_2026-11-04.{json,md}`)

| Métrica | Resultado |
|---|---|
| Total de estudios evaluados | **4** (todos Topcon 3D OCT-1000 MK2, incluidas copias) de 295 archivos; 291 sin adaptador |
| Casos sin lateralidad / por defecto | 0 / 4 |
| Fuentes concordantes (≥2 orígenes) | 0 / 4 |
| Conflictos entre fuentes | 0 / 4 |
| Con confirmación independiente | 0 / 4 |
| Emitido ≠ evidencia | 0 / 4 |
| Estado | declarada 4/4 (un solo origen: metadato nativo) |
| R2, R3, R4, R6 | 0/4 (**no hay mediciones**: contornos `RETINA_1..4` sin identificar) · R5: no aplica |

**Interpretación:** estos ceros **no validan nada**. Las 4 muestras son del mismo equipo y sin mediciones; Revo, Cirrus, Spectralis `.e2e` y Cirrus PDF **no fueron ejercitadas**
por el corpus público. Con un solo origen de lateralidad (el metadato nativo) es imposible observar un conflicto en esta ruta.

## 4. Validación en lumen (copia aislada, intérprete y dependencias del nodo; árbol de producción intacto)

| Comprobación | Resultado |
|---|---|
| SR idéntico con el log activo | **Sí** (1 341 nodos comparados; difieren solo 19 UIDs/fechas generados en cada ejecución) |
| Permisos | archivo 0600, directorio 0700 |
| PHI / valores en el log | ninguno (NOEL ID, nombre, fechas, UID, CMT buscados) |
| Interruptor `DISABLE` | detiene el registro sin reiniciar |
| Coste | ≈0,2 ms por registro (sin archivo que leer); `build_sr` completo ≈27 ms |
| Ensayo de `enable.sh` (simulacro → apply → idempotente → rollback) | correcto en una copia temporal |

Hallazgo operativo: en lumen, Transducin ejecuta **solo la API**, que no llama a `build_sr` ni al webhook; allí el log recogería ≈0 registros. Por eso **no modifiqué
el árbol de producción de lumen ni reinicié su servicio**. Los datos reales están en HP Z2, que no es accesible por SSH desde esta máquina (puerto 22 rechazado).

## 5. Despliegue (pendiente de ti en HP Z2)

`deploy/shadow/INSTALL.md` trae el procedimiento (PowerShell + NSSM), el interruptor y el rollback. Orden: **HP Z2 primero** (es donde hay emisiones reales); lumen solo si quieres
cubrir `retroactive_sr`. Tras unos días: `python scripts/shadow_report.py --md informe.md log <LOG_DIR>` genera el informe con las métricas pedidas
(estudios únicos, sin lateralidad/por defecto, concordantes, conflictos, confirmación independiente, R1–R6 por adaptador y destino, y limitaciones).

## 6. Limitaciones

- Sin emisiones reales todavía: **no hay frecuencias R1–R6 de producción**.
- Spectralis y Topcon: la lateralidad nativa es **un solo dato original**; solo el nombre de archivo (débil) puede contradecirla.
- Cirrus DICOM: los atributos estándar y privados se tratan como un solo origen; si el equipo los escribe de forma independiente, la concordancia real está subestimada (decisión conservadora).
- Revo: la geometría (tag 23) se lee del archivo solo si pesa menos de 400 MB y es accesible desde el nodo; si no, se registra como no disponible.
- No existen productores de confirmación médica ni de información independiente: el recuento es 0 por construcción hasta que se diseñen.
- Los estudios únicos se cuentan por HMAC de estudio; los reprocesos del SR pueden duplicar registros `sr`.
- Un conflicto o un `mismatch` no demuestran un ojo equivocado. Para eso se necesita la referencia independiente de la especificación de adquisición (localizador/SLO, reporte del fabricante, confirmación médica).
