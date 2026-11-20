# Validación en el equipo — lista de mediciones y verificaciones

Para la visita al equipo (Revo FC130 y, si se puede, Spectralis). Todo lo de aquí está **sin validar**:
las cifras de Transducin/Gass son plausibles, no confirmadas contra el equipo.

**Privacidad:** capturas y exportaciones sin nombre, fecha de nacimiento ni identificadores a la vista.
Nada de esto se commitea con datos del paciente. Anotar solo números y rótulos.

## 0. Qué traer / exportar
- [ ] Reporte macular completo de Revo (PDF o impreso) de **ambos ojos**, del mismo examen que los `.opt`.
- [ ] Reporte de **segmento anterior / paquimetría** de Revo del mismo examen (ambos ojos).
- [ ] Reporte de **Spectralis del ojo izquierdo** (hoy no lo tenemos; el derecho sí).
- [ ] Captura de pantalla del equipo con las capas segmentadas sobre un B-scan (sin datos del paciente).
- [ ] Si el software lo permite: exportar la tabla ETDRS y el mapa de grosor.
- [ ] Anotar la versión de software del equipo (Revo: ¿21.5.0?; Spectralis: versión de HEYEX).

## 1. Macular Revo — grosor y ETDRS
Hoy Gass muestra: grosor macular central 286.7 µm (ojo derecho) y 290.3 µm (ojo izquierdo), con la fóvea
estimada por geometría (Transducin, escaneo ancho de 12 mm con disco).

| Dato | Equipo | Gass | ¿Coincide? |
|---|---|---|---|
| Grosor subcampo central, ojo derecho (µm) | | 286.7 | |
| Grosor subcampo central, ojo izquierdo (µm) | | 290.3 | |
| Sectores internos: superior / temporal / inferior / nasal, ojo derecho | | (ver pantalla de Gass) | |
| Sectores externos, ojo derecho | | (ver pantalla) | |
| Ídem ojo izquierdo | | (ver pantalla) | |

- [x] **Orientación de sectores:** resuelto (ADR-033, ver `docs/revo-orientacion-y-geometria.md`). B-scan 0 superior; el A-scan crece
      hacia nasal en OD y hacia temporal en OS (marco crudo = marco del visor). Confirmado con el disco en datos crudos y con
      reportes del equipo, ambos ojos. El código anterior suponía lo contrario. Gass asume que el B-scan 0 es superior; la
      superposición ETDRS del visor debe revisarse contra esta orientación.
- [ ] **Posición de la fóvea:** ¿el equipo marca un centro foveal? Anotar sus coordenadas o mostrar dónde cae sobre el SLO.
      Gass lo estima con la foseta del grosor (a 3–6 mm del disco).
- [ ] **Qué superficies usa su grosor:** ¿ILM–RPE, ILM–Bruch o ILM–ISOS? Gass usa ILM→Bruch (BM − TOP).
- [ ] **Diferencia por definición:** el subcampo central es el disco de 1 mm; ¿el equipo promedia o toma un punto?

## 2. Escala física (mediciones con el caliper)
La escala sale del encabezado del archivo: 2.8 µm/píxel axial en macular, 3.724 µm/píxel en segmento anterior,
lateral = ancho de escaneo / A-scans (12 mm / 768 = 15.6 µm). **Nadie la ha verificado contra el equipo.**

Medir lo mismo en el equipo y en Gass (mismo corte, misma columna) y anotar:

| Medición | Corte n.º | Posición | Equipo (µm) | Gass (µm) | Diferencia |
|---|---|---|---|---|---|
| Grosor ILM–Bruch en la fóvea, ojo derecho | | | | | |
| Grosor ILM–Bruch en la fóvea, ojo izquierdo | | | | | |
| Grosor a 1 mm temporal de la fóvea | | | | | |
| Distancia horizontal entre dos puntos conocidos (p. ej. borde del disco a la fóvea) | | | | | |
| Grosor corneal central en segmento anterior, ojo derecho | | | | | |
| Grosor corneal central en segmento anterior, ojo izquierdo | | | | | |

- [ ] Repetir en Gass con **Fit**, **Fill** y **Crop**: debe dar el mismo valor (hoy el caliper obliga a 1:1).
- [ ] **Índice de refracción:** ¿el equipo divide la profundidad óptica entre un índice (≈ 1.376 en córnea,
      ≈ 1.38 en retina)? ¿Los 3.724 µm/píxel de segmento anterior ya están en tejido o en aire? Si no se incluye,
      la paquimetría saldría sobreestimada ≈ 38 %.
- [ ] Anotar la **resolución axial nominal** que declara el equipo y el **ancho de escaneo** de cada protocolo.

## 3. Segmento anterior Revo
- [ ] Paquimetría central, punto más delgado y sectores del mapa (ojo derecho e izquierdo).
- [ ] ¿Qué protocolo es (8 posiciones × 2560 A-scans, 8 mm)? ¿Radial, línea o cubo? Anotar nombre exacto.
- [ ] ¿El equipo da otras medidas del segmento anterior (profundidad de cámara, ángulo, queratometría)?
      Esas serían candidatas a SR. Hoy **no se genera SR** del segmento anterior.
- [ ] Un B-scan de los 32 no se decodifica (31 en un archivo): ¿qué corte falta?
- [ ] ¿Los contornos `TOP` y `BM` de estos volúmenes son epitelio y endotelio? Mostrarlo en pantalla. Con tus archivos propios, en los dos volúmenes de segmento anterior `TOP`, `NFL` y `BM` solo tienen datos en el 22–25 % de las posiciones y `TOP` y `BM` no coinciden en ninguna (parecen salida de la segmentación retiniana sobre una córnea), así que de ahí no se puede derivar paquimetría. La paquimetría de Revo vive en `RESULTS`, que está cifrado: hace falta su reporte.

## 4. Capas y contornos
En un B-scan con las capas visibles, anotar el nombre que el equipo da a cada línea y compararlo con Gass:

| Contorno de Gass | Nombre en el equipo | ¿Coincide? |
|---|---|---|
| TOP | ILM | |
| NFL | NFL/GCL | |
| GCL | GCL/IPL | |
| IPL | IPL/INL | |
| INL | INL/OPL | |
| OPL | OPL/ONL | |
| ONL | | |
| ELM | | |
| EZOS | | |
| ISOS | | |
| BM | Bruch | |

- [ ] **ELM y ONL:** en el encabezado están en un orden y en los datos en el otro; hoy ambos están marcados
      como «inferidos» y el ELM va sobre el ONL. Confirmar cuál es cuál.
- [ ] ¿El equipo segmenta la capa de Henle? (se decidió no llamarla Müller; falta decidir dónde vive).

## 5. Orientación y geometría
- [x] Ojo izquierdo del Revo: en pantalla, ¿lado nasal a la izquierda o a la derecha del B-scan? → a la izquierda (y a la derecha en
      el ojo derecho); resuelto con ADR-033 (disco en A bajo en OS, A alto en OD).
- [ ] ¿El corte 0 es superior? ¿Sentido del barrido (de arriba abajo)?
- [ ] Posición de los B-scans sobre el SLO del equipo (en qué línea del SLO cae el corte central) para validar
      la transformación escaneo→SLO que aún no decodificamos (`FNDSPHOTO`/`FNDCORR`).
- [ ] ¿La proyección en-face del equipo coincide en orientación con la de Gass (disco a la derecha en ojo derecho)?
- [ ] Espaciado real entre B-scans (mm) en el volumen de 256 cortes.

- [ ] **Registro con el SLO de Revo (`FNDSIR`)**: con tus dos macular de 12 mm, el disco queda a ≲ 0.3 mm del
      de la proyección en-face si se reescala a 632 px (≈ 52.7 px/mm); un ajuste por imagen (ECC) da traslaciones de
      2–15 px y escala ≈ 1, con correlación baja (0.16–0.21) porque `FNDSIR` es borrosa. **No validado.** En el equipo:
      anotar sobre su SLO dónde cae la línea del B-scan central y los extremos del cubo, y el campo de visión (mm) de `FNDSIR`.
- [ ] ¿La foto externa (`EYE`) tiene un corte horizontal en el equipo o solo en nuestra lectura? Comparar con su pantalla.

## 6. Capas internas y mapas que muestra el equipo
- [ ] RNFL y GCL+IPL por sectores (valores del reporte) para compararlos con los de Gass.
- [ ] Mapa de significancia y de desviación (−50…+50 %): anotar **los umbrales y colores** y contra qué base normativa
      se comparan (nombre de la base, edad, etnia). Esto decide si podemos usar semáforos «orientativos».
- [ ] Análisis de cambio: ¿contra qué examen compara por defecto? (el manual dice el más antiguo).

## 7. Spectralis (ojo izquierdo)
- [ ] CST y ETDRS completos del reporte del ojo izquierdo.
- [ ] Orientación de sectores del ojo izquierdo (nasal/temporal) y del SLO. (Para Spectralis el lado nasal sigue la convención de fondo
      de ojo y no está comprobado con el disco; ver `docs/revo-orientacion-y-geometria.md`, método en la sección 6.)
- [ ] Posición de la fóvea en el SLO.

## 8. Después de la visita
- Pasar las cifras a una tabla y yo las comparo con Gass; lo que difiera se corrige o se marca como no validado.
- No dar nada por validado hasta tener cifra del equipo y cifra de Gass en el mismo corte.
