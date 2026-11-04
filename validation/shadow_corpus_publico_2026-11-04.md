# Informe del modo sombra — lateralidad y riesgos R1–R6

Registros (emisiones): **4** · Estudios únicos por ruta: topcon_fda: 4

## 1. Lateralidad por ruta y destino

| Ruta | Destino | n | Sin lateralidad / por defecto | Fuentes concordantes (≥2 orígenes) | Concordancia independiente (≥2 fuertes) | Conflicto | Confirmada por referencia | Emitido ≠ evidencia |
|---|---|---|---|---|---|---|---|---|
| topcon_fda | sr | 4 | 0 (0 %) | 0 (0 %) | 0 (0 %) | 0 (0 %) | 0 (0 %) | 0 (0 %) |

Destino `sr`: una lateralidad vacía se emite como **"R"** (asignación por defecto). Destino `webhook`: se envía vacía.

## 2. Categorías de R1 y estado de la evidencia

| Ruta | Destino | R1 | Estado de la evidencia | Tipo de conflicto |
|---|---|---|---|---|
| topcon_fda | sr | {'unverified_single_source': 4} | {'declared': 4} | {} |

## 3. R2–R6 por ruta y destino

| Ruta | Destino | n | R2 solapados | R3 cuña nasal | R4 orientación | R5 centro geométrico | R6 sin procedencia |
|---|---|---|---|---|---|---|---|
| topcon_fda | sr | 4 | 0 (0 %) | 0 (0 %) | 0 (0 %) | {'not_applicable': 4} | 0 (0 %) |

## 4. Por tipo de estudio

| Ruta | Tipo | n | R1 | R5 |
|---|---|---|---|---|
| topcon_fda | macular | 4 | {'unverified_single_source': 4} | {'not_applicable': 4} |

## 5. Limitaciones y datos insuficientes

- **revo_opt: ruta no ejercitada** (0 registros). No se concluye nada; 0 hallazgos no es validación.
- **cirrus_dcm: ruta no ejercitada** (0 registros). No se concluye nada; 0 hallazgos no es validación.
- **cirrus_pdf: ruta no ejercitada** (0 registros). No se concluye nada; 0 hallazgos no es validación.
- **spectralis_e2e: ruta no ejercitada** (0 registros). No se concluye nada; 0 hallazgos no es validación.
- **topcon_fda: datos insuficientes** (4 estudios únicos < 10); porcentajes no interpretables.
- Evidencia no disponible (archivo ausente/ilegible/demasiado grande): ninguna.
- Un **conflicto** entre fuentes no es un ojo equivocado: demostrarlo exige una referencia independiente (localizador/SLO o confirmación médica).
- Spectralis y Topcon: el metadato nativo es **un solo dato original**; solo se suma el nombre de archivo (débil).
- Confirmación médica e información independiente de adquisición aún no tienen productor: su recuento es 0 por construcción.
- Los estudios únicos se cuentan por HMAC de estudio; una emisión aparece en `sr` y en `webhook`, y los reprocesos del SR pueden duplicar registros `sr`.
