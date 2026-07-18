# -*- coding: utf-8 -*-
# CORRE ESTO LOCALMENTE (no subir a Claude ni al repo con datos por-escaneo).
# Genera dos CSV de captura para ampliar el Bland-Altman actual (n=7 -> ~25-35):
#   - revo_capture_pairs.csv   (Site A, Revo FC130, meta ~15-20 pares)
#   - cirrus_capture_pairs.csv (Cirrus, meta ~10-15 pares)
#
# Cada fila trae el CMT ya calculado por el pipeline y una columna vacia
# ("native_reference_cmt_um") para que anotes a mano el valor nativo
# SOCT/Cirrus viendo el reporte de cada estudio.
#
# Ajusta las rutas y, si el script imprime nombres de columna que no
# coinciden con los que ya busca (device_site/device/study_type/cmt_um/
# noel_id), agrega el alias real a las listas CMT_KEYS / ID_KEYS abajo.

import csv

REVO_CSV = "full_corpus_results_v2.csv"
CIRRUS_CSV = "cirrus_full_results.csv"

REVO_TARGET_N = 20
CIRRUS_TARGET_N = 15

# IDs ya examinados en el Bland-Altman actual (usados en el n=7 final o
# descartados por razones ya conocidas: mala clasificacion, sin referencia
# nativa, errores de segmentacion) -- se excluyen para no re-seleccionarlos
# como si fueran nuevos.
ALREADY_USED = {
    "JAHJ19870831-OD",
    "JAHJ19870831-OS",
    "SITE_B_001",
    "SITE_B_003",
    "SITE_B_006",
    "SITE_B_007",
    "SITE_B_008",
    "SITE_B_009",
    "SITE_B_010",
    "SITE_B_011",
    "SITE_B_012",
    "SITE_B_013",
    "SITE_B_014",
    "SITE_B_017",
    "SITE_B_018",
}

CMT_KEYS = ("cmt_um", "CMT", "cmt", "central_macular_thickness_um", "cmt_value")
ID_KEYS = ("id", "noel_id", "NOEL_ID", "patient_id", "filename", "file", "study_id")
TYPE_KEYS = ("study_type", "acquisition_type", "scan_type")


def get_first(row, keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None


def is_macular(row):
    t = (get_first(row, TYPE_KEYS) or "").lower()
    return "macular" in t


def get_cmt(row):
    v = get_first(row, CMT_KEYS)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def capture(csv_path, out_path, target_n, device_label):
    rows_out = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        print(f"Columnas detectadas en {csv_path}: {reader.fieldnames}")
        for row in reader:
            if not is_macular(row):
                continue
            cmt = get_cmt(row)
            if cmt is None:
                continue
            ident = get_first(row, ID_KEYS)
            if ident in ALREADY_USED:
                continue
            rows_out.append(
                {
                    "id": ident,
                    "device": device_label,
                    "transducin_cmt_um": cmt,
                    "native_reference_cmt_um": "",  # <-- llenar a mano
                }
            )

    rows_out = rows_out[:target_n]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "device", "transducin_cmt_um", "native_reference_cmt_um"])
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"Escrito {out_path}: {len(rows_out)} filas (meta: {target_n})")


if __name__ == "__main__":
    capture(REVO_CSV, "revo_capture_pairs.csv", REVO_TARGET_N, "Revo FC130 (Site A)")
    capture(CIRRUS_CSV, "cirrus_capture_pairs.csv", CIRRUS_TARGET_N, "Cirrus")
    print("Listo. Abre los dos CSV generados, llena 'native_reference_cmt_um' a mano")
    print("viendo el reporte nativo de cada estudio, y guardalos para la siguiente sesion.")
