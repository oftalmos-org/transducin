#!/usr/bin/env python3
"""
Corre esto LOCALMENTE (no lo corro yo — full_corpus_results_v2.csv es dato
por-escaneo, politica de privacidad SS2.1 del monorepo).

Uso:
    python table3_site_breakdown.py

Lee full_corpus_results_v2.csv (debe estar en el mismo directorio, o pasa la
ruta como argumento) y escribe table3_site_breakdown.txt junto a el —
agregado, SIN PHI (solo conteos), seguro de compartir de vuelta.
"""

import csv
import sys
from pathlib import Path
from collections import Counter

CSV_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "full_corpus_results_v2.csv"
OUT_PATH = CSV_PATH.parent / "table3_site_breakdown.txt"

by_site_type = Counter()
by_site_device_type = Counter()
parse_ok = Counter()
parse_fail = Counter()

with CSV_PATH.open(encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        site = row["site"]
        device = row["device"]
        atype = row["acquisition_type"] or "unknown"
        success = row["success"] == "True"

        if success:
            parse_ok[site] += 1
            by_site_type[(site, atype)] += 1
            by_site_device_type[(site, device, atype)] += 1
        else:
            parse_fail[site] += 1

lines = []
lines.append("TABLE 3 / TABLE 4-5 SITE BREAKDOWN (agregado, sin PHI)")
lines.append("=" * 60)
lines.append("")
lines.append("-- Parse success/fail por sitio --")
for site in sorted(set(list(parse_ok) + list(parse_fail))):
    lines.append(f"  {site}: OK={parse_ok.get(site,0)}  FAIL={parse_fail.get(site,0)}")

lines.append("")
lines.append("-- Por sitio x tipo de adquisicion (para Tabla 3) --")
sites = sorted(set(s for s, _ in by_site_type))
types = sorted(set(t for _, t in by_site_type))
for t in types:
    row = [f"{by_site_type.get((s, t), 0):>4}" for s in sites]
    lines.append(f"  {t:<20} " + "  ".join(f"{s}={v.strip()}" for s, v in zip(sites, row)))

lines.append("")
lines.append("-- Por sitio x dispositivo x tipo (para Tabla 4/5, detalle Site B) --")
for (site, device, atype), n in sorted(by_site_device_type.items()):
    lines.append(f"  {site} / {device} / {atype:<18}  n={n}")

lines.append("")
lines.append(f"Sitios encontrados: {sites}")

OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
print(f"Escrito: {OUT_PATH}")
print("\n".join(lines))
