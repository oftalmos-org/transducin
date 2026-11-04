# Política de cobertura ETDRS por adaptador

Fecha: 2026-11-04 · Código: `transducin/etdrs_policy.py` · Tests: `tests/test_etdrs_policy.py`

## Problema
`revo_opt_reader.compute_etdrs` calcula los 9 sectores sin comprobar que el escaneo cubra el anillo. Con un cubo de
3,2 mm (los `.vol` de eyepydata) devuelve `S2/N2/I2/T2` usando solo las esquinas (≈16 % del anillo externo, que llega a
3 mm de radio) como si fuera el anillo completo.

## Diseño
- **No se toca `compute_etdrs` ni Revo ni Cirrus.** `compute_etdrs_with_policy()` lo llama tal cual y, si hay política,
  anula (`None`) los sectores no válidos. Con `policy=None` el resultado es exactamente el de `compute_etdrs`
  (test de igualdad).
- `EtdrsCoveragePolicy(name, min_coverage, min_valid_fraction)`, por adaptador:
  - `min_coverage`: fracción del área esperada del sector que cae dentro del volumen.
  - `min_valid_fraction`: fracción de los píxeles cubiertos con espesor válido (0 < espesor < 800 µm, igual que
    `compute_etdrs`).
- **Política por adaptador:** Revo/Cirrus → `LEGACY = None` (sin cambios). Spectralis y Topcon → `STRICT`
  (0,90 de cobertura, 0,80 de válidos). Se cambia con el argumento `etdrs_policy` de `measure_e2e_volume` /
  `measure_fda_volume` (los extractores legados lo dejan en `None`, igual que antes).
- Un sector no disponible queda en `None` y el adaptador añade una nota: «ETDRS no disponible por política 'strict':
  S2 (cobertura 9 %, válido 100 %), …». Si el centro (`C`) no es válido, `cmt_um` también queda en `None`.
- Un test compara las máscaras replicadas con `compute_etdrs` (incluye NaN y espesores negativos) para que no se desvíen.

## Resultados
| Caso | Antes | Con `STRICT` |
|---|---|---|
| Cubo 6 × 6 mm (Topcon UK Biobank) | 9 sectores | 9 sectores (cobertura ≥ 95 %) |
| Cubo 3,2 mm (eyepydata) | `S2/N2/I2/T2` con ≈16 % del anillo | `S2/N2/I2/T2 = None`; `C`, `S1/N1/I1/T1` disponibles |
| Mitad del cubo sin segmentación | sector con pocos píxeles | sector con < 80 % válido → `None` |

## Hallazgos que NO se corrigieron (afectan también a Revo y Cirrus)
1. **Los sectores S/N/I/T son medios anillos que se solapan**, no los cuadrantes de 90° del ETDRS estándar:
   `S = B<0`, `I = B>=0`, `N/T` según el eje A, cada uno intersecado con el anillo. Por eso el promedio de
   `S1,N1,I1,T1` coincide con la media del anillo. Los sectores nunca se compararon contra un informe del fabricante
   (el Bland-Altman previo solo validó el CMT).
2. **El centro es el del volumen, no la fóvea.** Pendiente de revisar, según lo acordado.
3. **Orientación sin verificar para Topcon:** `B-scan 0 = superior` y la dirección nasal/temporal se asumen; por eso el
   espesor experimental de Topcon no reporta sectores.
