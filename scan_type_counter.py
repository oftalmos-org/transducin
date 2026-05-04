#!/usr/bin/env python3
"""
scan_type_counter.py — Tabla 3: distribucion de tipos de escaneo en SOCT_DATA.

Itera todos los .opt recursivamente, lee OCTPARAMS (scan_protocol_id + dimensiones)
y deriva el scan_type usando las mismas heuristicas que opt_extractor.py.
Produce un CSV: scan_type | scan_protocol_id | dimensions | count | ejemplo_filename

Uso:
    python scan_type_counter.py [ROOT_DIR] [--csv OUTPUT.csv] [--verbose]

    ROOT_DIR por defecto: C:\\SOCT_DATA  (o el primer argumento)

Solo lectura. No modifica ningún archivo.
"""
from __future__ import annotations

import argparse
import csv
import struct
import sys
import zlib
from collections import defaultdict
from pathlib import Path

# ── Constantes del formato .opt ───────────────────────────────────────────────

_OPT_MAGIC = b"\xa5\xa5\xa5\xff"


# ── Parser mínimo de chunks (sin importar el módulo completo) ─────────────────

def _find_magic_positions(data: bytes) -> list[int]:
    positions = []
    start = 0
    while True:
        idx = data.find(_OPT_MAGIC, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 4
    return positions


def _parse_chunks(data: bytes) -> dict[str, dict]:
    positions = _find_magic_positions(data)
    chunks: dict[str, dict] = {}
    for i, p in enumerate(positions):
        pos = p + 4
        if pos + 4 > len(data):
            continue
        name_len = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if name_len == 0 or name_len > 64:
            continue
        name = data[pos: pos + name_len].rstrip(b"\x00").decode("ascii", errors="replace")
        pos += name_len
        if pos < len(data) and data[pos] == 0:
            pos += 1
        if pos + 8 > len(data):
            continue
        pos += 8  # meta + field2
        data_offset = pos
        next_magic = positions[i + 1] if i + 1 < len(positions) else len(data)
        real_size = next_magic - data_offset
        if name not in chunks or real_size > chunks[name]["real_size"]:
            chunks[name] = {"offset": data_offset, "real_size": real_size}
    return chunks


def _decompress(raw: bytes) -> bytes | None:
    if len(raw) < 6:
        return None
    bsize = struct.unpack_from("<I", raw, 1)[0]
    if bsize and bsize <= len(raw) - 5:
        try:
            return zlib.decompress(raw[5: 5 + bsize])
        except Exception:
            pass
    if raw[:2] in (b"\x78\x01", b"\x78\x9c", b"\x78\xda"):
        try:
            return zlib.decompress(raw)
        except Exception:
            pass
    return None


# ── Extracción de OCTPARAMS ───────────────────────────────────────────────────

def _extract_octparams(data: bytes, chunks: dict) -> dict:
    """Lee scan_protocol_id (tag 2), n_ascans (tag 5), n_bscans (tag 6) de OCTPARAMS."""
    result = {"scan_protocol_id": None, "n_ascans": None, "n_bscans": None}
    c = chunks.get("OCTPARAMS")
    if not c:
        return result
    raw = data[c["offset"]: c["offset"] + c["real_size"]]
    dec = _decompress(raw)
    if dec is None or len(dec) < 8:
        return result
    for i in range(0, len(dec) - 7, 8):
        tag = dec[i]
        typ = dec[i + 3]
        vraw = dec[i + 4: i + 8]
        if typ == 0x12:   # uint32
            val = struct.unpack_from("<I", vraw)[0]
        elif typ == 0x22:  # float32 — no relevante aquí
            continue
        else:
            continue
        if tag == 2:
            result["scan_protocol_id"] = int(val)
        elif tag == 5:
            result["n_ascans"] = int(val)
        elif tag == 6:
            result["n_bscans"] = int(val)
    return result


# ── Derivación de scan_type ───────────────────────────────────────────────────

# Sufijos de nombre de archivo → tipo (misma lógica que opt_extractor._TYPE_MAP)
_FNAME_TYPE_MAP = {
    "OCT":          "macular",
    "COLOR_FUNDUS": "fundus",
    "COLORFUNDUS":  "fundus",
    "BMETR":        "biometry",
    "ANGIO":        "angio",
    "TOPO":         "anterior",
    "ANTERIOR":     "anterior",
    "RNFL":         "rnfl",
    "OPTIC":        "optic_nerve",
}

_DATE8  = __import__("re").compile(r"^\d{8}$")
_TIME6  = __import__("re").compile(r"^\d{6}$")
_LAT_RE = __import__("re").compile(r"^(OD|OS)$", __import__("re").IGNORECASE)
_NOEL   = __import__("re").compile(r"^[A-Z]{3,4}\d{8}$", __import__("re").IGNORECASE)


def _type_from_filename(path: Path) -> str | None:
    """Extrae el tipo de scan del nombre del archivo (token no-date, no-lat, no-NOEL)."""
    stem = path.stem.upper()
    tokens = stem.split("_")
    # Filtrar tokens que son NOEL, fecha, hora, lateralidad, nombre
    type_tokens = []
    for t in tokens:
        if _NOEL.match(t) or _DATE8.match(t) or _TIME6.match(t) or _LAT_RE.match(t):
            continue
        # Si ya encontramos una fecha, los tokens anteriores son apellido/nombre
        type_tokens.append(t)

    # Buscar de atrás hacia adelante el primer token que sea un tipo conocido
    for t in reversed(type_tokens):
        key = t.replace("-", "_")
        if key in _FNAME_TYPE_MAP:
            return _FNAME_TYPE_MAP[key]
    # Si el stem contiene palabras clave como subcadena
    for key, val in _FNAME_TYPE_MAP.items():
        if key in stem:
            return val
    return None


def _type_from_dimensions(n_bscans: int | None, n_ascans: int | None) -> str | None:
    """Heurísticas de opt_extractor.py para clasificar por dimensiones."""
    if n_bscans is None or n_ascans is None:
        return None
    if n_bscans == 192 and n_ascans == 640:
        return "optic_nerve"
    if n_bscans in (319, 320) and n_ascans <= 320:
        return "angio"
    if n_ascans >= 4096:
        return "ultra_wide"
    if n_bscans <= 8 and n_ascans >= 1024:
        return "wide_field"
    if n_bscans <= 25 and n_ascans >= 512:
        return "hd_line"
    if n_bscans >= 100 and n_ascans >= 512:
        return "macular"
    return None


def _derive_scan_type(fname_type: str | None, n_bscans: int | None, n_ascans: int | None) -> str:
    """Combina fuentes en un scan_type canónico."""
    # La detección por dimensiones es más precisa para subtypes (wide_field, ultra_wide, hd_line)
    dim_type = _type_from_dimensions(n_bscans, n_ascans)

    if dim_type and dim_type not in ("macular",):
        # Subtypes que el nombre de archivo generaliza como "OCT"
        return dim_type
    if fname_type:
        return fname_type
    if dim_type:
        return dim_type
    if n_bscans and n_ascans:
        return f"unknown_{n_bscans}x{n_ascans}"
    return "unknown"


# ── Procesamiento de un archivo ───────────────────────────────────────────────

def process_opt(path: Path, verbose: bool = False) -> dict | None:
    """Lee un .opt y retorna su scan_type y metadatos. None si error."""
    try:
        data = path.read_bytes()
    except OSError as e:
        if verbose:
            print(f"  [SKIP] {path.name}: {e}", file=sys.stderr)
        return None

    if not data.startswith(_OPT_MAGIC):
        if verbose:
            print(f"  [SKIP] {path.name}: magic incorrecto", file=sys.stderr)
        return None

    chunks = _parse_chunks(data)
    params = _extract_octparams(data, chunks)

    fname_type = _type_from_filename(path)
    scan_type  = _derive_scan_type(
        fname_type,
        params["n_bscans"],
        params["n_ascans"],
    )

    return {
        "scan_type":        scan_type,
        "scan_protocol_id": params["scan_protocol_id"],
        "n_bscans":         params["n_bscans"],
        "n_ascans":         params["n_ascans"],
        "filename":         path.name,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", nargs="?", default=r"C:\SOCT_DATA",
                        help="Directorio raíz a escanear (default: C:\\SOCT_DATA)")
    parser.add_argument("--csv", default="scan_type_counts.csv",
                        help="Nombre del archivo CSV de salida (default: scan_type_counts.csv)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Mostrar progreso y errores por archivo")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"ERROR: directorio no existe: {root}", file=sys.stderr)
        sys.exit(1)

    # Acumuladores: scan_type → {count, ejemplos, protocol_ids}
    counts:    dict[str, int]      = defaultdict(int)
    examples:  dict[str, str]      = {}
    proto_ids: dict[str, set[int]] = defaultdict(set)

    opt_files = list(root.rglob("*.opt")) + list(root.rglob("*.OPT"))
    # Eliminar duplicados (rglob case-insensitive en Windows puede devolver el mismo archivo)
    seen_paths: set[Path] = set()
    unique_files: list[Path] = []
    for f in opt_files:
        resolved = f.resolve()
        if resolved not in seen_paths:
            seen_paths.add(resolved)
            unique_files.append(f)

    total = len(unique_files)
    print(f"Archivos .opt encontrados: {total}", file=sys.stderr)

    for i, path in enumerate(unique_files, 1):
        if args.verbose or i % 500 == 0:
            print(f"  [{i}/{total}] {path.name}", file=sys.stderr)

        result = process_opt(path, verbose=args.verbose)
        if result is None:
            counts["_error"] += 1
            continue

        st = result["scan_type"]
        counts[st] += 1
        if st not in examples:
            examples[st] = result["filename"]
        if result["scan_protocol_id"] is not None:
            proto_ids[st].add(result["scan_protocol_id"])

    # Ordenar por count descendente
    rows = sorted(
        [(st, counts[st]) for st in counts],
        key=lambda x: -x[1],
    )

    # Imprimir tabla en consola
    print("\n" + "=" * 65)
    print(f"{'scan_type':<20} {'count':>7}   {'scan_protocol_ids':<20}  ejemplo_filename")
    print("-" * 65)
    grand_total = 0
    for st, cnt in rows:
        if st == "_error":
            continue
        grand_total += cnt
        ids_str = ",".join(str(x) for x in sorted(proto_ids.get(st, set()))) or "—"
        ex = examples.get(st, "")
        print(f"{st:<20} {cnt:>7}   {ids_str:<20}  {ex}")
    print("-" * 65)
    print(f"{'TOTAL':<20} {grand_total:>7}")
    if counts.get("_error", 0):
        print(f"  (+ {counts['_error']} archivos con error/skipped)")
    print("=" * 65)

    # Escribir CSV
    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["scan_type", "count", "scan_protocol_ids", "ejemplo_filename"])
        for st, cnt in rows:
            if st == "_error":
                continue
            ids_str = ",".join(str(x) for x in sorted(proto_ids.get(st, set()))) or ""
            writer.writerow([st, cnt, ids_str, examples.get(st, "")])

    print(f"\nCSV guardado en: {args.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
