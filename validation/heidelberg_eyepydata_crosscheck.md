# Heidelberg `.vol` (eyepydata v1.0.0) — contraste de `compute_etdrs`

Fecha: 2026-11-03 · Datos: `MedVisBonn/eyepydata` v1.0.0 (MIT), `healthy_OD.vol` y `healthy_OS.vol`
leídos con eyepy 0.21.0 (venv aparte). **Esto NO valida el lector `.e2e`** y Transducin aún no tiene
lector `.vol`; se exportaron las capas `ILM` y `BM` (con su escala) y se pasaron a `compute_etdrs`.

## Datos
512 B-scans × 496 px × 512 A-scans; 3,87 µm axial; ≈6,3 µm lateral → cubo de ≈3,2 × 3,2 mm.

## Resultados
| | healthy_OD | healthy_OS |
|---|---|---|
| Central (`compute_etdrs`) | 257,1 µm | 255,9 µm |
| Central (numpy independiente, mismo radio 500 µm) | 257,1 µm | 255,9 µm |
| Media del anillo interno (independiente) vs promedio de S1/N1/I1/T1 | 332,8 vs 332,8 | 334,3 vs 334,3 |
| Mínimo de espesor respecto al centro del cubo | −25 µm (B), −94 µm (A) | −6 µm (B), +19 µm (A) |

Coincide el cálculo (la implementación independiente solo comprueba que no hay errores de código,
no la definición clínica del ETDRS).

## Hallazgos (el 1 ya tiene política para adaptadores nuevos; ver `etdrs_coverage_policy.md`)
1. **Anillo externo con cobertura parcial.** `compute_etdrs` devuelve S2/N2/I2/T2 aunque el cubo mida
   3,2 mm (el anillo externo llega a 3 mm de radio): solo ≈16 % del anillo cae dentro del cubo
   (las esquinas), pero se reporta una media como si fuera el anillo completo. Debería devolver
   `None` si la cobertura del anillo es insuficiente. En escaneos de 6 × 6 mm el anillo cabe entero
   y el resultado no cambiaría.
2. **Centro = centro del volumen, no la fóvea.** Aquí el mínimo de espesor queda a <0,1 mm del
   centro, pero no hay garantía en general; el spec pide fóvea localizada.
3. **Señal débil sobre temporal/nasal (n = 2 ojos de un sujeto).** En ambos ojos el sector temporal
   interno sale más grueso que el nasal (≈340 vs ≈326 µm), lo contrario de lo habitual en maculas
   sanas. Podría indicar que el mapeo T/N está invertido para volúmenes Heidelberg, o ser variación
   individual. Necesita más datos antes de concluir.
