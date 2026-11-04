# Topcon 3D OCT-1000 Mark II — contornos `RETINA_1..4` (UK Biobank, ejemplo `.fda`)

Fecha: 2026-11-03 · Estado del adapter: **experimental** · Datos: ejemplos públicos de UK Biobank
(`~/transducin-real-data/public/topcon/`, SHA-256 en `MANIFEST.json`). Este documento contiene solo
agregados numéricos; no hay metadatos identificables.

## 1. Lo que es cierto (medido en el archivo)

| Hecho | Valor |
|---|---|
| Equipo (`@HW_INFO_03.model_name`) | `3DOCT-1000MK2`, ROM 1.10 (mismo en el `.fds`) |
| Formato | Contenedor `FOCT` + `FDA`; 21 chunks; `@CONTOUR_INFO` ×4 |
| Volumen | 128 B-scans × 512 A-scans, imagen de 650 filas |
| Geometría (de `@PARAM_SCAN_04`) | 6 mm × 6 mm; 11,72 µm/A-scan; 46,9 µm/B-scan; 3,5 µm/px axial |
| Lateralidad (`@CAPTURE_INFO_02`) | izquierdo |
| Nombres de los contornos | `RETINA_1`, `RETINA_2`, `RETINA_3`, `RETINA_4` (no `MULTILAYERS_n`) |
| Datos por contorno | uint16 128×512, sin ceros (segmentación presente en todo el volumen); **32 bytes extra por chunk sin parsear** |
| `.fds` | **sin** `@CONTOUR_INFO`; volumen crudo en `@IMG_SCAN_03` (≈85 MB) |

Orden y orientación de B-scans: usando la fuerza de borde de la imagen (|ΔI| a ±4 px) sobre las
cuatro variantes (orden crudo/invertido × `H−v` / `v`), la variante **orden invertido + `H−v`**
(lo que hace `FDAReader`) gana: total 189,5 frente a 143,0 (orden crudo + `H−v`) y ≈100
(variantes `v`). Tres de los cuatro contornos la favorecen con claridad (60,1 vs 35,7; 54,1 vs
37,3; 48,9 vs 34,4); **`RETINA_3` es la excepción** (26,3 invertido vs 35,6 crudo; está a solo
≈6 px de `RETINA_2`, por lo que la ventana de ±4 px mezcla ambos).

## 2. Descripción de cada contorno (variante ganadora; filas desde arriba, 1 px = 3,5 µm)

| Contorno | Mediana (min–max) fila | Brillo 6 px arriba → abajo | Lectura descriptiva |
|---|---|---|---|
| `RETINA_1` | 268 (211–315) | 37 → 121 | transición oscuro→brillante hacia abajo |
| `RETINA_2` | 340 (260–371) | 76 → 153 | transición oscuro→brillante, 238 µm (192–291) bajo `RETINA_1` |
| `RETINA_3` | 345 (266–375) | 109 → 146 | 21 µm (10–28) bajo `RETINA_2`; sin cruces (≈1,5 % de las posiciones coinciden con `RETINA_2`) |
| `RETINA_4` | 356 (279–387) | 146 → 79 | transición brillante→oscuro hacia abajo; 42 µm (28–49) bajo `RETINA_3` |

- Los cuatro contornos se mantienen ordenados de arriba abajo, sin ningún cruce (≈1,5 % de las posiciones de `RETINA_3` coinciden con `RETINA_2`; distancia 0).
- Distancia `RETINA_1 → RETINA_4`: mediana 298 µm, mín. 224 µm, máx. 371 µm.
- El mínimo de esa distancia (224 µm) cae en el B-scan 62 de 128 y el A-scan 251 de 512, es decir,
  en el centro del cubo (dato solo geométrico).

## 3. Interpretación (NO verificada)

- La distancia `RETINA_1 → RETINA_4` tiene magnitud compatible con un espesor retiniano y su
  mínimo es central, lo que es coherente con una depresión foveal. Pero **no hay documento que
  defina** qué superficie es cada contorno, y las separaciones de `RETINA_2`/`RETINA_3`/`RETINA_4`
  (238, 21, 42 µm) no se explican con una asignación obvia.
- Con la documentación oficial revisada (K072971, K083316, IMAGEnet 6, protocolo de UK Biobank, y el
  manual de 3D OCT-1 citado por terceros) no se encontró un diccionario `RETINA_n` → superficie.
- UK Biobank menciona nueve fronteras segmentadas con TABS; el `.fda` solo trae cuatro líneas, por lo
  que probablemente no son salida de TABS (inferencia por conteo).


### Mapeo de un tercero (RETIMAT `read_fda.m`) — NO adoptado

El código fuente de RETIMAT (`src/io/read_fda.m`, MATLAB) mapea `RETINA_1 → ILM`, `RETINA_2 → IZ_RPE`,
`RETINA_3 → MZ_EZ`, `RETINA_4 → BM`. Es un mapeo de terceros, no documentación de Topcon, y el propio
código declara estar "heavily based on" los trabajos de ingeniería inversa y OCT-Converter, por lo que
**no es un parser independiente**.

Contraste con lo medido:

- Extremos: `RETINA_1` (oscuro arriba / brillante abajo) y `RETINA_4` (brillante arriba / oscuro abajo,
  a 298 µm de `RETINA_1` en mediana) son compatibles con una frontera interna y una externa. Esto es
  coherente con ILM y membrana de Bruch, pero la coherencia cualitativa no prueba la identidad.
- Intermedios: el mapeo pone `IZ_RPE` en `RETINA_2` y `MZ_EZ` en `RETINA_3`. Anatómicamente la zona
  elipsoide (`MZ_EZ`) está **por encima** de la interdigitación/RPE (`IZ_RPE`), pero en el archivo
  `RETINA_3` queda **por debajo** de `RETINA_2` en el 98,5 % de las posiciones. Esas dos etiquetas
  parecen intercambiadas o no se corresponden con lo que el equipo dibuja. Hasta aclararlo, los
  intermedios siguen sin identificar.

## 4. Decisión vigente

- `RETINA_1..4` se **leen, conservan y exponen** (`LayerVolume.contours`), con identidad anatómica
  desconocida. **No se calculan espesores** con ellos; el adapter deja `cmt_um = None`,
  `measurement_provenance = ""` y la nota `UNIDENTIFIED: …`.
- Cuando existan espesores calculados por Transducin sobre contornos del fabricante, su procedencia
  es `vendor_contour_derived` (no `vendor_reported`), independiente de `extraction_confidence`.

## 5. Qué falta para validación anatómica

1. Una referencia que defina las superficies (documentación de Topcon para este software, un
   lector independiente que las nombre, o un DICOM exportado por la herramienta oficial con
   segmentación identificada).
2. Los 32 bytes extra por chunk `@CONTOUR_INFO`.
3. Un lector `.fds` propio solo si se necesitan imagen/metadatos del `.fds` (no trae contornos).

## Reproducir

```bash
TRANSDUCIN_REAL_DATA_DIR=~/transducin-real-data .venv314/bin/python -m pytest -m real_data -q
```

## 6. Investigación (2026-11-03): origen de los contornos y mediciones

### ¿Los contornos son del equipo, de otro algoritmo o inferidos por lectores?
- **No los infiere ningún lector:** están almacenados en los 4 chunks `@CONTOUR_INFO` del propio archivo
  (uint16 128×512 cada uno); los lectores solo los decodifican.
- **Los escribió software de Topcon:** cada chunk termina con una cadena ASCII de 32 bytes `3.21.000.00`
  (los «32 bytes extra»), igual a `@FDA_FILE_INFO`; `@MAIN_MODULE_INFO` declara `3DOCT-1000.exe`
  3.21.1.11. En la cabecera de cada contorno `method = 0` y `format = 0` (significado no documentado).
- **No se sabe qué algoritmo los produjo.** TABS (Yang et al., Opt. Express 2010, PMC3101081) es un
  algoritmo de investigación de nueve fronteras (1 ILM, 2 NFL/GCL, 3 GCL/IPL, 4 IPL/INL, 5 INL/OPL,
  6 ELM, 7 IS/OS, 8 OS/RPE, 9 BM/coroides), implementado en Matlab y C, validado sobre volúmenes de
  3D OCT-1000 (128 cortes de 480×512, 6 × 6 mm, 3,5 µm/px) y con parámetros optimizados para imágenes de
  Topcon. **El artículo no menciona el formato `.fda` ni que el software del equipo use TABS**, y el
  archivo trae 4 líneas, no 9. No se deben confundir los identificadores `boundary 1..9` de TABS con
  `RETINA_1..4`.
- Dato del artículo (Fig. 3): TABS detecta **primero ILM e IS/OS** (alto contraste) y después
  BM/coroides y OS/RPE. Esas cuatro son las fronteras más robustas de TABS.

### ¿Trae el FDA mediciones calculadas por el fabricante?
**No se encontró ninguna** en los 18 chunks del archivo:

| Chunk | Contenido (verificado) |
|---|---|
| `@FAST_Q2_INFO` | 6 valores de calidad de imagen (float32): 0,02 · 38,1 · 75,9 · 83,0 · 81,2 · 273,3 (sin interpretar) |
| `@REGIST_INFO`, `@EFFECTIVE_SCAN_RANGE` | rectángulos en coordenadas de imagen del fondo de ojo (p. ej. 686,438 → 1379,1132), no espesores |
| `@GLA_LITTMANN_01` | ceros y −1 |
| `@IMG_PROJECTION`, `@THUMBNAIL`, `@IMG_FUNDUS` | imágenes |
| `@PARAM_OBS_02` | 6 bytes (variante corta) |
| `@PATIENT_INFO_02`, `@PATIENTEXT_INFO` | **no inspeccionados a propósito** (identidad); no se puede descartar un valor numérico en `@PATIENTEXT_INFO` (64 B) sin parsearlo sin imprimir |

No hay chunk de espesor, ETDRS ni fóvea. El espesor se calcula en el software al abrir el estudio y no se guarda.

### Hallazgo de formato
`@IMG_JPEG` no contiene JPEG: cada corte es un flujo **JPEG 2000** (firma `FF 4F FF 51`, 8 bits gris,
650×512). Pillow los decodifica; los navegadores no. El visor los transcodifica a PNG sin pérdida.

## 7. Hipótesis sobre las cuatro superficies (visualización, NO validada)

Perfil medio de brillo alineado a `RETINA_1` (88 cortes × 98 columnas centrales; fila relativa, 1 px = 3,5 µm):

| Posición de la línea (rel. a `RETINA_1`) | Perfil de brillo alrededor |
|---|---|
| `RETINA_1` (0 px) | salto de ≈12 a ≈110–124 (vítreo oscuro → tejido brillante) |
| `RETINA_2` (+68 px, 238 µm) | inicio de la subida (≈62 → 110) hacia una banda brillante externa |
| `RETINA_3` (+74 px, 259 µm) | mitad de esa subida (≈92 → 133) |
| `RETINA_4` (+85 px, 298 µm) | borde descendente de la banda (pico de 142 en +83 px; 136 → 117) |

- **H1:** `RETINA_1 = ILM`, `RETINA_2 = IS/OS`, `RETINA_3 = OS/RPE`, `RETINA_4 = BM/coroides` (las cuatro
  primeras que detecta TABS). Es coherente con las separaciones (ILM→IS/OS ≈ 238 µm, IS/OS→OS/RPE ≈ 21 µm,
  OS/RPE→BM ≈ 42 µm) y con el brillo, **pero a esta resolución la banda externa (EZ/IZ/RPE) no se separa**,
  así que no la distingue de otras asignaciones.
- **Mapeo de RETIMAT** (`RETINA_2 = IZ_RPE`, `RETINA_3 = MZ_EZ`) invierte el orden anatómico respecto a lo
  medido; sería compatible solo si Topcon usara esos nombres con otro significado.
- Los extremos (`RETINA_1` = ILM y `RETINA_4` = BM) se adoptan **solo como hipótesis provisional para
  visualización**, no para calcular espesores.
- Siguiente paso de verificación: revisión visual en `scripts/topcon_contour_viewer.py` (el visor guarda las
  interpretaciones por separado de las etiquetas nativas, con método y estado).

## 8. Revisión visual del especialista (2026-11-04, 1 lector)

Con el visor `scripts/topcon_contour_viewer.py` el especialista asignó y confirmó visualmente las cuatro líneas:
`RETINA_1 = ILM`, `RETINA_2 = IS/OS`, `RETINA_3 = OS/RPE`, `RETINA_4 = BM`. La anotación (sin los campos de
prueba `annotator = TEST` y «prueba de nota», restos de una prueba automática) y su exportación sin tocar están
en `~/transducin-real-data/public/topcon/viewer/annotations/`, referenciadas en el `MANIFEST.json`.

Se mantienen **dos niveles de validación distintos**:

| Nivel | Estado |
|---|---|
| Revisión visual del especialista (identidad anatómica sobre los B-scans) | **hecha** — 1 lector |
| Validación independiente del significado original del campo binario `RETINA_n` | **pendiente** — ningún documento de Topcon lo define |

El mapeo vive en `transducin/topcon_retina_map.py` como **hipótesis versionada e inmutable** (v0.1.0); no lo usan
el lector ni el adapter de producción.

## 9. Yang et al. 2011 (PMC3184859) y su relación con el software 3.21

«Automated segmentation of outer retinal layers in macular OCT images of patients with retinitis pigmentosa»
(Biomed. Opt. Express 2011): cuatro fronteras en volúmenes de **Topcon 3D OCT-1000** — 1 ILM (vítreo/ILM),
2 IS/OS, 3 OS/RPE, 4 BM/coroides. Coincide en número, orden y nomenclatura con la hipótesis H1. Error contra
un segmentador manual experimentado (88 escaneos de RP; µm, sin signo): ILM 3,57 ± 2,50 · IS/OS 3,92 ± 1,15 ·
OS/RPE 3,82 ± 1,59 · BM/coroides 4,63 ± 2,26.

**Coincidencias demostradas:** mismo equipo; cubo de 128 B-scans × 512 A-scans, 6 × 6 mm, 3,5 µm/px (igual al
`.fda`); mismas cuatro superficies en el mismo orden.

**Diferencias (no se afirma que sean idénticos):**
1. Es un algoritmo de investigación (doble gradiente + camino mínimo; Matlab 2010b para la cuantificación),
   no el software `3DOCT-1000.exe 3.21.1.11` del archivo; el artículo **no menciona** el formato `.fda`, los
   nombres `RETINA_n` ni que el equipo ejecute ese algoritmo.
2. Población: pacientes con retinitis pigmentosa (aquí, un ejemplo normal de UK Biobank). Con OS que se
   adelgaza hasta cero, la definición práctica de IS/OS y OS/RPE puede diferir de la de un ojo sano.
3. Orden de detección en el artículo: ILM, IS/OS, BM/coroides y por último OS/RPE; el del software se desconoce.
4. Fecha: el algoritmo es de 2011; el software 3.21 del archivo puede ser anterior o posterior, no se pudo fechar.
5. Misma familia que TABS (Yang 2010), que da 9 fronteras; el `.fda` solo trae 4.
6. El artículo usa 3D OCT-1000 en la visita inicial y 3D OCT-2000 en el seguimiento; el archivo es un Mark II.

Conclusión: **evidencia bibliográfica de nomenclatura/orden consistente con H1**, no prueba de equivalencia.

## 10. Plausibilidad de la separación `RETINA_3` → `RETINA_4` (128 B-scans)

| Medida | Valor |
|---|---|
| Mediana / media / DE (todo el volumen) | 42,0 / 39,8 / 7,8 µm |
| p1 · p5 · p95 · p99 | 14,0 · 28,0 · 49,0 · 56,0 µm |
| Posiciones entre 28 y 56 µm | 94,8 % (> 56 µm: 0,5 %; < 14 µm: 0,8 %) |
| Mediana por B-scan | mín. 28,0 – máx. 45,5; DE entre B-scans 3,8 µm |
| Zonas de B-scan (0–31 · 32–63 · 64–95 · 96–127) | 38,5 · 42,0 · 42,0 · 38,5 µm |
| Zonas de A-scan (de izquierda a derecha) | 35,0 · 42,0 · 42,0 · 38,5 µm |
| Centro del cubo vs borde | 46,1 vs 35,5 µm |
| Correlación con espesor total ILM→BM | 0,25 (con `RETINA_2`→`RETINA_3`: −0,16) |
| Cruces (`RETINA_4` sobre `RETINA_3`) | 0 |

- En el A-scan que el especialista revisó, 46 µm coincide con la región central, donde la separación es la
  mayor (46,1 µm de media); el valor no es atípico, y es estable entre B-scans.
- **Plausibilidad anatómica (orientativa; cifras de la literatura de memoria, sin verificar):** 42–46 µm es algo
  mayor que el grosor típico RPE + membrana de Bruch (del orden de 15–30 µm). Es coherente con que `RETINA_3`
  quede en el borde superior del complejo hiperreflectante RPE/zona de interdigitación y no en el borde interno
  del RPE; a 3,5 µm/px y con la óptica de 2009 esas bandas no se separan. Además, `RETINA_2`→`RETINA_4` es de
  ≈60 µm (mediana 59,5), compatible con la distancia IS/OS→BM. El aumento central de la separación se parece al
  de estructuras que engrosan en la fóvea. **Esto no distingue las interfaces externas**; solo muestra que
  el valor no contradice H1.
- Dato clave para diferenciar: `RETINA_3`→`RETINA_4` varía con la posición (35 → 46 µm) y `RETINA_2`→`RETINA_3`
  casi no (21 µm).

## 11. Espesor ILM→BM experimental (solo 3D OCT-1000 Mark II)

Código: `transducin/topcon_experimental.py`, `transducin/topcon_retina_map.py`, `scripts/topcon_experimental_thickness.py`
(requiere `--experimental`); adapter: `TopconFdaAdapter(experimental=True).experimental_thickness(path)`.

- Usa solo `RETINA_1` y `RETINA_4` sin modificarlos; `measurement_provenance = vendor_contour_derived`; la
  identidad (revisión del especialista + bibliografía; correspondencia binaria no confirmada) queda en cada resultado.
- **No** alimenta `cmt_um`, el SR, DICOM cuantitativo ni Graefe (`publishable_as_clinical = False`).
- Verifica la escala axial (3,5 µm/px, de `PARAM_SCAN_04` y de la bibliografía) y se niega a calcular si difiere;
  se niega con otros modelos o sin los contornos. Marca contornos ausentes, geometría inválida (BM no por debajo de la
  ILM), valores fuera de 50–800 µm, B-scans con <90 % de válidos y violaciones de orden de los contornos intermedios.
- Resultado en el ejemplo de UK Biobank (exploratorio): 100 % válido, 0 posiciones marcadas; media 299,1 µm,
  mediana 297,5 µm (p5–p95: 255,5–350,0); disco central de 0,5 mm de radio (centro geométrico) 287,0 µm; anillo
  0,5–1,5 mm 342,4 µm; mínimo 224 µm. El mapa muestra una depresión foveal central con anillo parafoveal más grueso.
- Sin sectores S/N/I/T (orientación del volumen Topcon no verificada) y centro geométrico, no foveal.
- Incertidumbre orientativa: si el software se comportara como el algoritmo de la bibliografía, la diferencia de
  borde con un segmentador manual es de ≈3,6 µm (ILM) y ≈4,6 µm (BM); no está demostrado para este software.
