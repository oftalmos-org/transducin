# Especificación de adquisición de estudios reales — Spectralis, SOLIX, Triton (2026-11-04)

Objetivo: que cada sesión de adquisición produzca un conjunto completo e incorporable al corpus sin trabajo posterior de reconstrucción.
Orden de prioridad: **Heidelberg Spectralis → Optovue/Visionix SOLIX → Topcon Triton**. Se comienza con estudios propios del titular; el estudio
multivendor con el Colegio de Oftalmólogos se diseñará después (§8).

## 1. Qué obtener, idealmente del **mismo ojo** y la misma jornada

| # | Elemento | Por qué | Prioridad |
|---|---|---|---|
| 1 | **Volumen nativo completo** del equipo | Fuente primaria; contiene contornos y metadatos propietarios | Esencial |
| 2 | **DICOM volumétrico** (OPT, Ophthalmic Tomography) | Comparar lo que el equipo exporta con lo que contiene el nativo | Esencial |
| 3 | **Segmentación original** (en el nativo y/o exportada) | Primera fuente de verdad del fabricante | Esencial |
| 4 | **Reporte del fabricante** (PDF/imagen) con CST y rejilla ETDRS | Referencia independiente para validar nuestro cálculo | Esencial |
| 5 | **Imagen localizadora** (SLO, IR, fondo) con la posición de los B-scans | Verificar lateralidad y orientación sin suposiciones | Esencial |
| 6 | **Metadatos de adquisición** (ver §3) | Procedencia | Esencial |
| 7 | **Posición foveal original** si el equipo la guarda o la muestra | Evita usar el centro geométrico | Cuando exista |
| 8 | Exportación de **mapas de espesor** del fabricante (numérica o imagen) | Contraste del mapa derivado | Deseable |
| 9 | Repetición inmediata del mismo ojo (≥1 adquisición adicional) | Repetibilidad | Deseable, en un subconjunto |

## 2. Protocolo de adquisición

- **Preferido, no obligatorio:** volumen macular de ≈ 6 × 6 mm. Se aceptan otros tamaños: **conservar y registrar el protocolo realmente utilizado**
  (dimensiones físicas, número de B-scans, A-scans por B-scan, separación entre B-scans, promedio/ART/Eye-tracking, modo de alta resolución o rápido).
- No ajustar a mano el centrado para "parecerse" entre equipos: anotar la fijación y si el equipo marcó la fóvea.
- Un ojo por carpeta de sesión. Ojo derecho / ojo izquierdo declarado por el operador **y** anotado antes de abrir el archivo (para contrastar luego con los metadatos).
- Calidad: registrar el índice de calidad del equipo (p. ej. Q, SSI, SQI) y cualquier artefacto visible.

## 3. Hoja de captura por adquisición (rellenar en el momento)

```
colección_id:            (p. ej. spectralis_2026-11-xx_sujeto01)
equipo / modelo / número de serie:
versión de software (adquisición y exportación):
fecha y hora de adquisición mostradas por el equipo (y zona horaria del equipo):
ojo (derecho/izquierdo) declarado por el operador:
protocolo (nombre exacto en el equipo) y parámetros: tamaño mm, nº B-scans, A-scans, separación, ART/promedio
fijación / centrado / ¿fóvea marcada por el equipo? (sí/no, coordenadas si se ven)
CST del reporte (µm) y valores ETDRS del reporte (anotar tal como aparecen; foto/PDF del reporte)
índice de calidad del equipo:
calibración / escalas mostradas (µm/píxel axial, mm/píxel lateral) si el equipo las muestra:
formatos exportados (lista) y ruta de exportación usada:
observaciones (movimiento, parpadeo, patología conocida):
```

## 4. Formatos de exportación por equipo

La lista es de intención; **cada exportación se declara tal como se obtuvo** (no asumir qué contiene cada formato). Si un equipo no puede exportar algo, se anota "no disponible" en la hoja de captura.

| Equipo | Nativo | DICOM | Segmentación | Reporte | Localizador |
|---|---|---|---|---|---|
| **Spectralis** | `.e2e` y/o `.sdb`/`.vol` (el que permita el software), con anotaciones/segmentación incluidas | Exportación DICOM OPT del software | Contornos dentro del `.e2e`/`.vol`; exportación XML/CSV si existe | PDF del reporte de espesor retiniano con rejilla ETDRS | SLO/IR (`.vol` lo incluye; exportar imagen) |
| **SOLIX** | Archivo nativo del Visionix/Optovue (`.OCT` / formato de la versión instalada) | DICOM OPT | Contornos nativos; exportación del mapa de espesor | PDF de espesor retiniano con ETDRS | Imagen de fondo/IR del equipo |
| **Triton** | `.fda` (y `.fds` si existe) | DICOM OPT | `MULTILAYERS_n` dentro del `.fda` | PDF del reporte macular | Imagen de fondo / color de la misma sesión |

## 5. Estructura de carpetas y nombres (sin información personal)

```
$TRANSDUCIN_REAL_DATA_DIR/private/<equipo>/<colección_id>/
    native/           ← archivos tal como salen del equipo, sin renombrar
    dicom/
    segmentation/
    report/
    localizer/
    sheet.yaml        ← hoja de captura (§3)
    MANIFEST.json     ← ver §6
```

- El nombre del sujeto es un seudónimo (`sujeto01`); no incluir nombre, fecha de nacimiento ni NOEL ID en nombres de archivo.
- **No renombrar ni reexportar los nativos.** Si un nombre contiene PHI, se hace una copia de trabajo y se conserva el original en el almacén de originales con su hash (ADR-024 propuesto).
- Nada de esto entra en un repositorio Git (`input/`, `Output*/` y el corpus están fuera de Git).

## 6. `MANIFEST.json` por colección

Campos de CLAUDE.md §2.1.1 más los necesarios para el registro de originales:

```json
{
  "collection_id": "",
  "manufacturer": "", "model": "", "serial_number": "", "software_version_acquisition": "", "software_version_export": "",
  "format_real": "",
  "files": [ { "path": "", "sha256": "", "size_bytes": 0, "role": "native|dicom|segmentation|report|localizer" } ],
  "acquisition_datetime_original": "", "acquisition_timezone": "",
  "laterality_declared_by_operator": "", "laterality_in_metadata": "",
  "protocol": { "size_mm": "", "n_bscans": 0, "n_ascans": 0, "bscan_spacing": "", "averaging": "" },
  "vendor_reported": { "cst_um": null, "etdrs": null, "fovea_position": null, "quality_index": null },
  "source": "estudio propio del titular",
  "license_or_access": "uso propio; consentimiento del titular",
  "deidentification_status": "no desidentificado | desidentificado parcial | desidentificado verificado",
  "validation_status": "sin validar",
  "claude_session_ok": false
}
```

`claude_session_ok` permanece en `false` salvo autorización expresa y copia desidentificada (CLAUDE.md §2.1.1 y §9.4). Quitar nombre y fecha de nacimiento **no** desidentifica.

## 7. Verificación al ingresar una colección (solo hashes y estructura, sin leer contenido clínico)

1. Calcular SHA-256 de cada archivo y comparar con el manifiesto.
2. Comprobar que existen los seis elementos esenciales (§1) o que están marcados como "no disponible".
3. Comprobar que `laterality_declared_by_operator` está presente y que `sheet.yaml` coincide con el manifiesto.
4. Fijar `validation_status: "sin validar"`; la validación se hace después y por separado.
5. Procesar **localmente** (`lumen`/`Photon`); ninguna sesión externa lee los archivos privados.

## 8. Datasets de acceso controlado y estudio multivendor

- **AI-READI Mini** (solicitud presentada; contenedor privado en Azure configurado; todas las carpetas excepto FLIO): **acceso pendiente.** No se descarga ni se procesa nada hasta que lo apruebe. Al recibirlo se trata como *datos de investigación de acceso controlado sujetos a su licencia*: almacén **separado** (`$TRANSDUCIN_REAL_DATA_DIR/controlled/ai-readi/`), nunca dentro de `public/` ni en un corpus redistribuible, nunca expuesto a servicios externos de IA, con su propia entrada de manifiesto y sin commits. Descripción de la investigación coherente con la solicitud: diabetes tipo 2, interoperabilidad y análisis cuantitativo de OCT en enfermedad retiniana diabética.
- **Estudio multivendor posterior** (Colegio de Oftalmólogos): cada participante podrá estudiarse en varios equipos durante una misma jornada, sin necesidad de que todos coincidan el mismo día. Requiere protocolo, criterios de inclusión, consentimiento, protección de datos, revisión ética aplicable y plan estadístico antes del reclutamiento. Tamaños orientativos del handoff: fase técnica 5–10 ojos por equipo; piloto ≈ 20–30. Esta especificación se usará como plantilla de la hoja de captura de ese estudio.
