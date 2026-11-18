# Revo FC130: orientación, centro y geometría de las regiones

Resumen técnico de las decisiones ADR-033 (aceptada) y ADR-023 (propuesta, ETDRS v2) que afectan al código de Transducin. Los ADR
completos viven en el repositorio de arquitectura de RetinaOS; aquí está lo que necesita quien lea o modifique este repositorio.

## 1. Qué se corrigió y por qué

El código suponía que, en Revo, el índice de A-scan crece hacia **temporal** en el ojo derecho y hacia **nasal** en el izquierdo.
Los datos dicen lo contrario, por lo que ETDRS, mRNFL, mGCIPL y pRNFL de Revo salían con nasal y temporal intercambiados.

**Orientación validada (`transducin.etdrs.REVO_VALIDATED`):** el B-scan 0 es superior; el índice de A-scan crece hacia nasal en OD y
hacia temporal en OS. Dicho de otro modo, el marco crudo tal como se muestra (superior arriba, A creciente a la derecha) **es el del
visor**: nasal a la derecha en OD y a la izquierda en OS. `LEGACY_ASSUMED` conserva el supuesto anterior solo para comparaciones.

## 2. Evidencia

Se usaron los `.opt` de un sujeto (ambos ojos) y los reportes impresos por el propio equipo (software 21.5.1) de esos mismos
estudios:

| Prueba | Resultado |
|---|---|
| Posición del disco óptico en datos crudos (volumen widefield 12×12 que lo incluye) | OD: disco en A alto (≈493) y fóvea en ≈220; OS: disco en A bajo (≈289) y fóvea en ≈558 |
| pRNFL, 12 sectores horarios, anillo de 3.4 mm | RMS contra el reporte: 4.7–4.8 µm con el marco correcto, 24–36 µm con el supuesto anterior |
| ETDRS widefield (medias de 9 regiones) | RMS 0.9 µm contra el reporte con el código corregido (sin él, nasal y temporal difieren 40–55 µm) |
| 3D macular 10×10 (el reporte muestra **mínimos** por sector) | Un mínimo no puede superar la media de la misma región: con el supuesto anterior el mínimo nasal externo del reporte (324 y 309 µm) superaba la media guardada (≈300 µm); con la orientación validada no hay violaciones |
| CMT 3D macular | 289.3 y 286.9 µm (reporte: 289 y 287) |

Superior/inferior no se ven afectados: el disco queda 0.4–0.7 mm por encima de la fóvea en ambos ojos, coherente con B-scan 0
superior.

## 3. Geometría que reproduce el reporte del fabricante

- **Regiones ETDRS disjuntas** (cuadrantes de 90° centrados en S/N/I/T) en lugar de medios anillos que se solapaban (ADR-023).
- **Círculo central de radio 525 µm** (CMT y ETDRS-C). A 500 µm el valor queda ~4 µm por debajo del reporte en ambos ojos. Es un
  ajuste empírico; el fabricante no documenta el radio.
- **Anillo pRNFL en radio 1750 µm y 100 µm de grosor** (diámetro interno 3.40 mm; el reporte indica 0.10 mm de espesor). Antes:
  1700 ± 100 µm, con sesgo de +4 µm.
- **Centro de medición.** Widefield con disco: fóvea estimada (`detect_fovea`). 3D macular sin disco (≥ 6 mm): foseta estimada
  (`find_foveal_pit`: mínimo del espesor ILM→BM suavizado en una ventana de 1.5 mm; se acepta con un contraste ≥ 40 µm frente a un
  anillo de 0.8–1.5 mm; si no, centro del volumen). En el ojo izquierdo el centro del volumen estaba ~170 µm de la foseta y movía el
  CMT de 286.0 a 291.8 µm. El método queda registrado en el DICOM (`(0009,1023)`, `method`).

## 4. Uso en el código

Las funciones compartidas (`compute_etdrs`, `compute_rnfl_sectors`, `compute_gcl_ipl`) aceptan `orientation=` (opcional). Sin él se
conserva el comportamiento heredado, que siguen usando Spectralis, Topcon y Cirrus. El flujo de Revo (`opt_extractor`) pasa
`REVO_VALIDATED`. `compute_peripapillary_rnfl` es solo de Revo y aplica el marco validado y el anillo nuevo directamente.

## 5. Lo que sigue sin validar (ADR-023)

- **Spectralis/Heidelberg:** el B-scan 0 inferior se verificó con las posiciones del archivo; el lado nasal sigue la convención
  de fondo de ojo y **no está comprobado con el disco**. El extractor reordena los volúmenes E2E a la convención heredada de
  `compute_etdrs` (`_orient_to_etdrs_convention`), de modo que reordenamiento y regla heredada se compensan. Un reporte propio del
  ojo izquierdo (mapa de espesor 6 mm) lo respalda: centro −1.3 µm, anillo interno dentro de ±4 µm y nasal > temporal en ambos
  anillos como en el reporte; el anillo externo difiere −13 a +18 µm (sin explicar; volumen de 6 mm con 25 B-scans y el anillo en el
  borde del escaneo). El pRNFL de un circle scan del mismo ojo queda a ≤ 3.3 µm del reporte (global, superior, inferior, nasal,
  temporal; nasal ≈ temporal, así que los cuatro cuadrantes no distinguen un espejo). El ojo derecho no se comparó.
- **Topcon:** orientación y centro sin validar; el espesor ILM→RPE/BM es experimental.
- **Cirrus:** `_etdrs_from_map` usa cuñas con la fóvea del DICOM, pero su cuña nasal se corta y fija nasal/temporal por posición en la
  imagen para ambos ojos.
- **ETDRS v2 (`etdrs.py`)** sigue sin emitirse como segunda serie; hace falta emisión dual y versionado del SR.
- **Disco óptico:** el C/D de Transducin (promedio de vertical y horizontal con umbral de copa de 150 µm) no equivale al del
  reporte del fabricante y sobreestima la copa; un umbral ≈ 250 µm reproduce el C/D vertical y el área del borde, pero no el volumen
  de copa.

## 6. Cómo repetir la validación con el reporte de otro equipo

1. Ubicar el disco óptico en los datos crudos de un volumen que lo incluya y compararlo con la fóvea: eso fija de qué lado crece
   el A-scan hacia nasal en cada ojo (no depende del reporte).
2. Calcular el pRNFL en 12 sectores horarios en el marco del visor y en su espejo, y comparar el RMS con los 12 valores del
   reporte: los sectores difieren mucho entre sí y el espejo se distingue sin ambigüedad (la media global no cambia con un espejo).
3. En ETDRS, comprobar la regla «mínimo del reporte ≤ media de la misma región» en las regiones nasal y temporal externas.
4. Buscar el centro que minimiza el error contra el reporte y compararlo con el centro del volumen y con la foseta estimada.

## 7. Impacto en datos existentes

Los SR de Revo generados antes de este cambio tienen nasal/temporal invertidos y la geometría anterior (CMT con radio de 500 µm y,
en 3D macular, centrado en el volumen). Deben regenerarse con la versión corregida antes de comparar series longitudinales.
