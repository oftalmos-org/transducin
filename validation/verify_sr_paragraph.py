# -*- coding: utf-8 -*-
"""Verifica las cifras del parrafo de Limitations (4.5) del Paper 1.

Lee SOLO validation/corpus_audit_results.csv (no cruza con pipeline_results.csv:
rel_folder guarda la carpeta, no el archivo, asi que no son empatables).

No abre archivos .opt, no requiere pydicom, y no imprime ningun noel_id ni
ninguna ruta: solo conteos agregados.

Uso:
    python validation/verify_sr_paragraph.py
"""

import csv
import collections
import os

SITE_A = "CUU"
PATH = "validation/corpus_audit_results.csv"


def truthy(v):
    return str(v).strip().lower() in ("true", "1", "yes", "si", "sí", "ok")


def has_val(v):
    return bool(str(v).strip()) and str(v).strip().lower() not in ("none", "nan", "null")


if not os.path.exists(PATH):
    raise SystemExit("No existe %s - corre corpus_audit.py primero." % PATH)

with open(PATH, encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

a = [r for r in rows if r["site"] == SITE_A]

print("=" * 74)
print("SITE A (%s) en corpus_audit: %d archivos" % (SITE_A, len(a)))
print("acquisition_type distintos:", sorted({r["acquisition_type"] for r in a}))
print("=" * 74)
print()

hdr = "%-16s %5s %5s %5s %5s %5s | %6s %6s" % (
    "acquisition_type",
    "tot",
    "cmt",
    "etdrs",
    "rnfl",
    "biom",
    "ALGUNA",
    "NINGUNA",
)
print(hdr)
print("-" * len(hdr))

none_by_type = collections.Counter()
sqi_none = collections.defaultdict(list)
sqi_some = collections.defaultdict(list)
tot_none = tot_some = 0

for at in sorted({r["acquisition_type"] for r in a}):
    g = [r for r in a if r["acquisition_type"] == at]
    n_cmt = sum(1 for r in g if has_val(r["cmt_um"]))
    n_etdrs = sum(1 for r in g if truthy(r["etdrs_present"]))
    n_rnfl = sum(1 for r in g if truthy(r["rnfl_present"]))
    n_biom = sum(1 for r in g if truthy(r["biometry_present"]))
    some = none = 0
    for r in g:
        any_m = (
            has_val(r["cmt_um"])
            or truthy(r["etdrs_present"])
            or truthy(r["rnfl_present"])
            or truthy(r["biometry_present"])
        )
        try:
            sqi = float(r["sqi_mean"])
        except ValueError:
            sqi = None
        if any_m:
            some += 1
            if sqi is not None:
                sqi_some[at].append(sqi)
        else:
            none += 1
            if sqi is not None:
                sqi_none[at].append(sqi)
    none_by_type[at] = none
    tot_none += none
    tot_some += some
    print("%-16s %5d %5d %5d %5d %5d | %6d %6d" % (at, len(g), n_cmt, n_etdrs, n_rnfl, n_biom, some, none))

print("-" * len(hdr))
print("%-16s %5d %5s %5s %5s %5s | %6d %6d" % ("TOTAL", len(a), "", "", "", "", tot_some, tot_none))
print()

MEAS = [at for at in none_by_type if any(k in at.lower() for k in ("macul", "optic", "nerve", "biometr"))]
print("=" * 74)
print("MEASUREMENT-CAPABLE SIN NINGUNA MEDICION (el numero del parrafo)")
print("=" * 74)
sub = 0
for at in sorted(MEAS):
    print("  %-16s %3d" % (at, none_by_type[at]))
    sub += none_by_type[at]
print("  %-16s %3d   <-- debe ser 92 (52 macular + 32 optic_nerve + 8 biometry)" % ("SUMA", sub))
print()

print("=" * 74)
print("TEST DE LA HIPOTESIS SQI")
print("=" * 74)
print("El filtro SQI solo puede matar el CMT (se calcula de pixeles del B-scan).")
print("NO puede matar ETDRS ni RNFL, que vienen de chunks ya guardados.")
print("Por tanto un archivo tumbado SOLO por SQI conserva ETDRS/RNFL -> SI genera SR.")
print()
sqi_victims = [r for r in a if not has_val(r["cmt_um"]) and (truthy(r["etdrs_present"]) or truthy(r["rnfl_present"]))]
print("Archivos sin CMT pero CON etdrs/rnfl (sospechosos de SQI, pero SI generan SR): %d" % len(sqi_victims))
by = collections.Counter(r["acquisition_type"] for r in sqi_victims)
for at, n in by.most_common():
    print("    %-16s %3d" % (at, n))
print()
print("Si este numero es > 0, esos archivos NO estan entre los 92 fallos,")
print("y por tanto el filtro SQI no explica ninguno de ellos: el parrafo se sostiene.")
print()


def stats(label, xs):
    if not xs:
        return
    xs = sorted(xs)
    print("  SQI %-30s n=%-4d min=%.2f  mediana=%.2f  max=%.2f" % (label, len(xs), xs[0], xs[len(xs) // 2], xs[-1]))


print("=" * 74)
print("SQI: los que no tienen NINGUNA medicion vs los que si (macular/optic_nerve)")
print("=" * 74)
for at in sorted(MEAS):
    stats("%s / sin medicion" % at, sqi_none[at])
    stats("%s / con medicion" % at, sqi_some[at])
print()
print("Si el SQI de los 'sin medicion' es NORMAL (parecido al de los 'con medicion'),")
print("confirma que fallaron porque los chunks NO ESTAN, no porque la senal fuera mala.")
print("Ese es exactamente el argumento del parrafo.")
print()

# Marcadores de error agregados (solo etiquetas cortas sin rutas: no hay PHI)
print("=" * 74)
print("MARCADORES DE ERROR en los 'sin ninguna medicion' (measurement-capable)")
print("=" * 74)
errs = collections.Counter()
for r in a:
    if r["acquisition_type"] not in MEAS:
        continue
    any_m = (
        has_val(r["cmt_um"]) or truthy(r["etdrs_present"]) or truthy(r["rnfl_present"]) or truthy(r["biometry_present"])
    )
    if any_m:
        continue
    e = str(r.get("error", "")).strip()
    if "\\" in e or "/" in e or len(e) > 70:
        e = "(mensaje largo o con ruta - omitido)"
    errs[e or "(sin mensaje)"] += 1
for e, n in errs.most_common(12):
    print("  %3d  %s" % (n, e))
