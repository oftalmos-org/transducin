# Informe comparativo — Topcon experimental, ETDRS legacy frente a v2, efectos en Revo y Cirrus

Fecha: 2026-11-04 · Estado: **para revisión; sin commit, sin push, sin cambios en resultados clínicos actuales.**
Datos: ejemplos públicos de UK Biobank (Topcon 3D OCT-1000 Mark II, 1 ojo izquierdo) y `eyepydata` v1.0.0 (Heidelberg,
2 ojos de un sujeto), en el corpus local fuera de Git. Revo y Cirrus **no** se ejecutaron con datos clínicos (§2.1 de `CLAUDE.md`):
su auditoría es de código y con campos sintéticos.

## 1. Resumen

1. **Evidencia Topcon nueva:** TrueMap v2.12 (feb. 2008) y TrueMap Measurement Software (ene. 2009) documentan cuatro capas
   (ILM, IS/OS, RPE, membrana de Bruch); un estudio de 2019 muestra que el espesor del algoritmo integrado de Topcon se mide
   **de la ILM al borde anterior del RPE**. Fortalece H1; **no demuestra** el mapeo binario de `RETINA_n`.
2. **Dos espesores experimentales** (ILM→BM y ILM→RPE candidato) están disponibles solo en el módulo experimental. Difieren por un
   desfase casi constante de ≈45 µm en la zona central y ≈35 µm en la periferia; la profundidad de la depresión foveal es la misma
   (55 contra 57 µm). Ninguno es un valor de Topcon.
3. **El ETDRS legacy tiene un defecto geométrico real:** sus sectores son medios anillos que se solapan (cada píxel del anillo
   cuenta en dos sectores). Se implementó una versión nueva con nueve regiones disjuntas (`transducin/etdrs.py`), con tests de
   geometría y datos reales. En ojos normales la corrección mueve los sectores entre 1 y 7 µm; en patología asimétrica podría ser
   mayor (no medido).
4. **La orientación pesa más que la geometría:** equivocar el lado nasal cambia el sector nasal/temporal entre 12 y 23 µm. La
   orientación **no está validada** para Topcon (el registro contra el fondo de ojo fue inconcluso) y solo parcialmente para
   Heidelberg.
5. **Cirrus tiene un defecto propio:** su sector nasal solo usa el 51 % de su cuña (semi-cuña). Revo hereda el defecto de los medios
   anillos. El valor central (CMT) no se ve afectado en ninguno de los dos.
6. Para habilitar salida clínica a Graefe faltan, como mínimo, las pruebas de la sección 7.

## 2. Evidencia Topcon (documentada)

| Fuente | Fecha | Qué dice (verificado) | Qué NO dice |
|---|---|---|---|
| Topcon TrueMap v2.12 (ophthalmologyweb) | 8-feb-2008 | «nuevo módulo de retina de **cuatro capas**» para el 3D OCT-1000 | No nombra las capas |
| TrueMap Measurement Software, autorización FDA (ophthalmologyweb) | 15/16-ene-2009 | «visualizar **cuatro capas**»: ILM, IS/OS, RPE y membrana de Bruch | No define el espesor ni el formato `.fda` |
| Chen et al., Exp. Ther. Med. 2019 (PMC6488992) | 2019 | Volúmenes `.fds` del OCT-1000 (software **2.20**) reanalizados en un OCT-2000 (**8.11.003.04**). Los límites interno y externo del espesor son «el borde anterior de la ILM y el borde anterior del RPE» | No habla de la membrana de Bruch ni de `RETINA_n`; no usa el software 3.21 |
| Yang et al. 2011 (PMC3184859) | 2011 | Cuatro fronteras (ILM, IS/OS, OS/RPE, BM/coroides) en 3D OCT-1000 | Es un algoritmo de investigación en Matlab; no menciona `.fda` |

- **Lo que fortalece:** que el equipo y su software documentaron cuatro capas con esos nombres, y que el espesor oficial de Topcon
  termina en el RPE, no en la membrana de Bruch.
- **Lo que no demuestra:** el archivo del ejemplo es de software **3.21** (`3DOCT-1000.exe 3.21.1.11`), distinto de 2.12, 2.20 y
  8.11. Ningún documento relaciona `RETINA_n` con una superficie.
- **`RETINA_3`:** se trata como **posible límite RPE / OS-RPE**, sin fijar una definición más precisa. La distancia a `RETINA_4`
  (mediana de 42 µm) es algo mayor que el grosor típico de RPE + Bruch, lo que es compatible con un borde en la parte alta del
  complejo RPE/zona de interdigitación.

## 3. Los dos espesores experimentales Topcon

Ambos usan los contornos originales sin modificarlos, con `measurement_provenance = vendor_contour_derived`, escala axial
verificada (3,5 µm/px), 100 % de posiciones válidas y sin contornos cruzados. Solo disponibles con `experimental=True`.

| | ILM→BM (`RETINA_1→4`) | ILM→RPE candidato (`RETINA_1→3`) | Diferencia (`RETINA_3→4`) |
|---|---|---|---|
| Media / mediana (µm) | 299,1 / 297,5 | 259,2 / 259,0 | 39,8 / 42,0 |
| p5–p95 (µm) | 255,5–350,0 | 220,5–304,5 | 28–49 |
| Mínimo–máximo (µm) | 224–371 | 175–346,5 | 0–84 |
| Disco central (r = 0,5 mm, centro geométrico) | 287,0 | 240,3 | 46,7 |
| Anillo 0,5–1,5 mm | 342,4 | 297,0 | 45,4 |
| Profundidad foveal (anillo − centro) | 55,4 | 56,7 | — |

- Correlación entre los dos mapas: **0,962**. La forma de la depresión foveal es la misma: el desfase es casi uniforme.
- La diferencia es mayor en el centro (≈46 µm) que en la periferia del cubo (≈35 µm).
- En el mapa de diferencia aparecen **sombras de vasos** como líneas donde la separación se estrecha (< 25 µm): artefactos de
  segmentación, más visibles en ese mapa que en los de espesor.
- **Cuál se parece más a lo que mide Topcon:** si `RETINA_3` fuera el borde anterior del RPE, ILM→RPE sería la definición de
  Topcon (Chen 2019). No se demuestra; por eso los dos se muestran y ninguno se presenta como valor del fabricante.
- Figura: `~/transducin-real-data/public/topcon/experimental/thickness_map.png`; estadísticas: `thickness_stats.json`.

## 4. ETDRS: geometría legacy frente a v2

| | Legacy (`compute_etdrs`) | v2 (`transducin/etdrs.py`) |
|---|---|---|
| Sectores S/N/I/T | Medios anillos: `S = B<0`, `I = B>=0`, `N/T` por el eje A; **se solapan** (factor 2,0 medido en los anillos internos) | Cuadrantes de **90°** (cuñas diagonales), disjuntos y que cubren el anillo completo |
| Áreas | no coinciden con el ETDRS estándar (cada sector es el doble) | coinciden con la geometría analítica (< 1 %); cuatro cuñas iguales |
| Escala física | µm, un solo `lateral_um`; `bscan_um` supone cubo cuadrado | µm por eje (píxeles anisotrópicos) |
| Orientación | supuesta: B-scan 0 superior; OD nasal a la izquierda | **parámetro explícito** con estado `validated` y origen |
| Centro | centro del volumen | objeto con origen: `geometric` / `foveal_candidate…` / `foveal_registered` |
| Cobertura | ninguna (política opcional aparte) | por región: área cubierta y fracción válida; no disponible → `None` |
| «CMT» | `C` | nunca se llama CMT sin centro foveal registrado **y** validado |

### 4.1 Efecto de la geometría (misma orientación; ojos normales)

| Región | Topcon ILM→BM | Topcon ILM→RPE | Heidelberg OD | Heidelberg OS |
|---|---|---|---|---|
| C | 0,0 | −0,0 | −0,2 | +0,1 |
| S1 | +3,4 | +3,6 | +1,5 | +2,1 |
| N1 | **−6,8** | **−7,0** | **−6,4** | **−6,8** |
| I1 | +4,9 | +5,4 | +3,3 | +3,4 |
| T1 | −1,3 | −2,0 | +1,4 | +1,4 |
| S2 / N2 / I2 / T2 | +2,9 / −2,2 / −3,1 / +2,4 | +4,8 / −2,7 / −3,7 / +1,9 | no disponibles | no disponibles |

(Δ = v2 − legacy, en µm.) El centro no cambia. Los demás sectores se mueven entre 1 y 7 µm en estos ojos normales; con una
lesión asimétrica la diferencia puede ser mayor, pero **no se midió**.

### 4.2 Orientación y lateralidad

- **Heidelberg `.vol` (verificado en el archivo):** el B-scan 0 está en la parte **inferior** del SLO (y = 4,16 mm) y el último en
  la superior (y = 0,94 mm), en los dos ojos; los A-scans crecen de izquierda a derecha. Esto contradice el supuesto legacy
  «B-scan 0 = superior».
- **Lado nasal en Heidelberg:** el disco no sale en el campo del SLO, así que no se pudo ver. Con la convención de fondo de ojo
  (OD nasal a la derecha, OS a la izquierda) el sector nasal interno sale **más grueso** que el temporal en los dos ojos, como en
  una mácula sana; con el supuesto legacy sale al revés. Es una comprobación de consistencia (2 ojos de un sujeto), no una prueba.
- **Topcon:** el registro de la proyección OCT contra el fondo de ojo fue **inconcluso** (correlación ≤ 0,05, sin margen entre
  las 8 transformaciones), así que la orientación no se pudo verificar. La v2 la exige como parámetro y avisa.
- **Sensibilidad (rango de cada sector entre las 4 orientaciones posibles, µm):**

| | S1 / I1 | N1 / T1 | S2 / I2 | N2 / T2 |
|---|---|---|---|---|
| Topcon ILM→BM | 5,8 | **12,8** | 5,0 | **21,0** |
| Heidelberg OD | 8,0 | **23,3** | — | — |
| Heidelberg OS | 2,9 | **22,9** | — | — |

  Equivocar el lado nasal pesa más (12–23 µm) que el cambio de geometría (1–7 µm).

### 4.3 Cobertura

- Cubo de 6 × 6 mm (Topcon): las 9 regiones con cobertura completa.
- Cubo de 3,2 mm (Heidelberg): el anillo externo cubre solo el **16 %** → `S2/N2/I2/T2` no disponibles en v2. El legacy los
  reportaba con ese 16 %.
- En tests sintéticos con el centro desplazado 1,6 mm, la cobertura difiere entre sectores (`N2` cae por debajo del umbral y `T2`
  conserva más cobertura que `N2`): se evalúa por sector, no por anillo.

### 4.4 Centro foveal

| | Candidato foveal respecto al centro geométrico (µm; B, A) | Disco central: geométrico → candidato |
|---|---|---|
| Topcon ILM→BM | −23, +6 | 287,0 → 286,5 |
| Heidelberg OD / OS | (−3, −72) / (−3, +79) | 256,9 → 255,7 / 256,0 → 255,0 |

En estos ojos normales y centrados el efecto en el valor central es ≤ 1,2 µm. Eso **no** valida el centro: el candidato (mínimo de
espesor suavizado) sigue marcado como no validado y `clinical_central_eligible = False`.

## 5. Auditoría de Revo y Cirrus (solo código y datos sintéticos)

| | Revo (`.opt`) | Cirrus (DICOM) | Cirrus (PDF) |
|---|---|---|---|
| Función | `compute_etdrs` (legacy), en `opt_extractor` | `_etdrs_from_map` propia | OCR de los valores del fabricante |
| Centro `C` / CMT | no afectado | no afectado | no aplica |
| Sectores S/N/I/T | **afectados**: medios anillos solapados | cuñas de 90°, pero **N1/N2 solo usan el 51 % de la cuña** (`arctan2` ≤ 180°, la cuña va de 135° a 225°) | no afectados |
| Centro de la grilla | centro del volumen | fóvea leída del DICOM (`FoveaPosition`) si existe; si no, el centro | — |
| Orientación | supuesta (B-scan 0 superior; OD nasal izquierda) | N = izquierda y T = derecha para **ambos ojos**; en `sr_builder` no se encontró ninguna permutación por lateralidad | — |
| Laterality por defecto | `read_opt` llama a `compute_etdrs` sin lateralidad (`"L"`); `opt_extractor` recalcula con la lateralidad solo `if cd.laterality` | — | — |

- **Cirrus, comprobación sintética:** con 400 µm en la mitad no incluida de la cuña nasal (−180°…−135°), `N1` informa 300 µm; el
  valor correcto sería 348,9 µm (subestimación de 48,9 µm). La otra mitad, `T1`, `S1` e `I1`, sí se calculan completas.
- **Qué podría estar afectado en lo ya emitido:** los sectores `S1,N1,I1,T1,S2,N2,I2,T2` de los SR de Revo y de Cirrus (DICOM), con
  los rótulos «Superior/Nasal/Inferior/Temporal inner/outer». `C` y el CMT no. Si en Cirrus OD el N/T están permutados es algo que
  **no se pudo confirmar** sin datos; el comentario del código dice que la lateralidad «se aplica en el SR builder», y no se
  encontró.
- **No auditado (privacidad):** los valores reales de la clínica y los CSV de `validation/`; hacerlo exige ejecución local con
  salida solo agregada.

### Migración propuesta (sin ejecutar nada)
1. Mantener la ruta legacy sin cambios y añadir los sectores v2 **junto a** los legacy, etiquetados por método y versión
   (`etdrs_method`), sin reemplazar el SR actual.
2. Validar por equipo contra una referencia independiente: Cirrus tiene los valores del fabricante en el PDF (OCR) y en su
   DICOM; Revo, el informe del equipo. Comparar sector por sector, con Bland-Altman.
3. Validar la orientación por equipo (SLO o fondo de ojo + proyección) antes de emitir S/N/I/T.
4. Reprocesar el archivo histórico: los contornos están en los `.opt` y en los DICOM de Cirrus, así que no hace falta volver a
   adquirir.
5. Versionar el SR y avisar a Gass (los rótulos cambian de significado, no de nombre); decidir cuándo se retira el legacy.
6. ADR propuesto: `ADR-023-etdrs-v2-cuadrantes-no-solapados.md` (en `01-Architecture/ADRs/`, repo raíz).

## 6. Bioptigen (limitaciones)

- El `.OCT` de MATLAB Central es nativo y se convierte, pero **no trae calibración ni lateralidad ni contornos**: no hay
  espesores posibles.
- `oct-converter` escribe `PixelSpacing = [0,02, 0,02] mm` y `SliceThickness = 0,2 mm` como valores provisionales (20 µm/px no es
  plausible para este equipo). Con `boct_converter`: sin calibración explícita se avisa en `ImageComments` («SIN CALIBRAR … no
  usar para medir») y en consola; con `--pixel-spacing-mm`/`--slice-thickness-mm` se registran como indicados explícitamente.
- La lateralidad solo vale con un token `OD`/`OS` aislado en el nombre o con `--laterality`; se declara su procedencia.

## 7. Qué falta para permitir salida clínica hacia Graefe

| Área | Falta |
|---|---|
| Identidad de las capas | Correspondencia binaria `RETINA_n` ↔ superficie con una fuente independiente (documentación de Topcon, o un DICOM del exportador oficial con segmentación nombrada); segundo lector |
| Orientación | Validar por equipo (Topcon: sin resolver; Heidelberg: ver el disco) y fijarla en el adaptador |
| Centro | Foveal registrado y validado (Cirrus ya trae `FoveaPosition`); hasta entonces no se llama CMT |
| Exactitud | Comparar con el informe del fabricante (TrueMap, IMAGEnet, Spectralis) en una muestra de ojos normales y patológicos, con Bland-Altman y tolerancias por equipo; hoy hay **un solo ojo** de Topcon y **dos** de Heidelberg |
| Robustez | Comportamiento con segmentación fallida, edema, drusas, sombras de vasos, calidad baja; repetibilidad |
| Calibración | Escala lateral y axial verificadas por equipo (Topcon: axial verificada); Bioptigen sin calibración |
| Conformidad | SR/DICOM validados con `dciodvfy` y `dicom-validator`; emitir `measurement_provenance` y la versión del método |
| Contrato con Graefe | Mapeo FHIR R4 (Observation) con procedencia, versión de ETDRS y estado de validación; ADR del flujo |
| Regresión | Tests que demuestren Revo y Cirrus sin cambios mientras no se migre; auditoría local con datos reales (solo agregados) |
| Gobernanza | Firma del médico (`CLAUDE.md` §9.2); revisión de `claude_session_ok` por colección privada |

## 8. Cambios en el código (todos sin conectar a salidas clínicas)

- `transducin/etdrs.py` (nuevo): ETDRS v2. `tests/test_etdrs_v2.py` (20 tests), `tests/test_real_data_etdrs.py` (datos reales).
- `transducin/topcon_experimental.py` y `transducin/topcon_retina_map.py` (v0.2.0): segundo cálculo ILM→RPE candidato,
  comparación y evidencia nueva; `scripts/topcon_experimental_thickness.py` dibuja los tres paneles.
- `scripts/compare_etdrs_legacy_v2.py`: genera las tablas de la sección 4.
- `transducin/boct_converter.py`: aviso de calibración en consola.
- Revo y Cirrus: **sin cambios**.
- Tests: 128 pasan con el corpus local; 108 pasan y 20 se omiten con Python 3.11 sin los datos reales. `ruff` limpio.
