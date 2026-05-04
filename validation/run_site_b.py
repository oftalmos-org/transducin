#!/usr/bin/env python3
"""
Validación cross-version Site B (Querétaro)
Procesa .OPT de SOCT 11.5.0 y 11.5.3, reporta éxito/fallo por archivo.
SIN modificar parser, SIN commitear archivos al repo.
"""

import csv
import logging
import os
import traceback
from pathlib import Path

SITE_B_ROOT = Path(os.environ.get("SITE_B_ROOT", "/data/input/site_b"))
REVO60_DIR  = SITE_B_ROOT / "REVO60"
REVO130_DIR = SITE_B_ROOT / "REVO130"
OUTPUT_CSV  = Path(__file__).parent / "site_b_results.csv"

logging.basicConfig(level=logging.WARNING)


def _safe_float(val) -> str:
    if val is None:
        return ""
    try:
        return f"{float(val):.1f}"
    except Exception:
        return str(val)


def _chunks_from_parsed(data: bytes) -> list[str]:
    """Extrae nombres de chunks top-level usando parse_opt_chunks."""
    try:
        from transducin.revo_opt_reader import parse_opt_chunks
        chunks = parse_opt_chunks(data)
        return sorted(chunks.keys())
    except Exception:
        return []


def process_file(opt_path: Path, device: str, soct_version: str) -> dict:
    result = {
        "filename":         opt_path.name,
        "device":           device,
        "soct_version":     soct_version,
        "file_size_mb":     round(opt_path.stat().st_size / 1024 / 1024, 1),
        "success":          False,
        "magic_bytes_ok":   False,
        "n_frames":         None,
        "bscan_shape":      "",
        "scan_type_params": "",
        "chunks_found":     "",
        "unknown_chunks":   "",
        "cmt_um":           None,
        "etdrs_present":    False,
        "rnfl_present":     False,
        "biometry_present": False,
        "cdr":              None,
        "sqi_mean":         None,
        "study_uid":        "",
        "error":            None,
        "traceback":        "",
    }

    # Chunks conocidos del formato .opt Revo según ingeniería inversa de Transducin
    known_chunks = {
        "OCTPARAMS", "PARAMS", "APARAMS", "DMARKERS", "TOMOSQI", "TRAJ",
        "MYOPI", "FNDCORR", "FNDSPHOTO", "FNDCORRLINK", "FNDSSTNGS", "FNDSPREVIEWPARAMS",
        "PATIENT.DAT", "PARAMS.DAT", "PREVIEW.DAT",
        "EYE", "SLO", "PRV", "FNDSRECO", "FNDSIR", "ANGPRV",
        "TOP", "NFL", "GCL", "IPL", "INL", "OPL", "ONL", "ELM", "EZOS", "ISOS", "BM", "BOTTOM",
        "CSI", "FIT", "ALIGN",
    }

    try:
        from transducin.revo_opt_reader import read_opt, _OPT_MAGIC, parse_opt_chunks

        raw = opt_path.read_bytes()

        if raw[:4] == _OPT_MAGIC:
            result["magic_bytes_ok"] = True
        else:
            result["error"] = f"Magic incorrecto: {raw[:4].hex()}"
            return result

        # Chunks top-level antes del read_opt completo
        try:
            chunks_dict = parse_opt_chunks(raw)
            chunk_names = sorted(chunks_dict.keys())
            result["chunks_found"] = "|".join(chunk_names)

            # Chunks que no son T-chunks (Tn) ni A-chunks (An) ni conocidos
            unknown = [
                c for c in chunk_names
                if c not in known_chunks
                and not (len(c) >= 2 and c[0] in ("T", "A") and c[1:].isdigit())
            ]
            result["unknown_chunks"] = "|".join(sorted(unknown))
        except Exception as e:
            result["chunks_found"] = f"ERROR:{e}"

        parsed = read_opt(opt_path)

        result["success"]    = True
        result["n_frames"]   = parsed.get("n_frames", 0)
        result["study_uid"]  = parsed.get("study_uid") or ""

        shape = parsed.get("shape", (0, 0))
        result["bscan_shape"] = f"{shape[1]}x{shape[0]}"  # width x height

        params = parsed.get("params") or {}
        n_bscans  = params.get("n_bscans", "?")
        n_ascans  = params.get("n_ascans", "?")
        sw_mm     = params.get("scan_width_mm", "?")
        axial_mmpx = params.get("axial_um", "?")
        result["scan_type_params"] = f"bscans={n_bscans} ascans={n_ascans} sw={sw_mm}mm axial={axial_mmpx}mm/px"

        cmt = parsed.get("cmt_um")
        if cmt is not None:
            result["cmt_um"] = round(float(cmt), 1)

        result["etdrs_present"]    = parsed.get("etdrs") is not None
        result["rnfl_present"]     = parsed.get("rnfl") is not None
        result["biometry_present"] = parsed.get("myopi") is not None

        cdr = parsed.get("cdr")
        if cdr is not None:
            result["cdr"] = round(float(cdr), 3)

        sqi = parsed.get("sqi")
        if sqi is not None and hasattr(sqi, "mean"):
            result["sqi_mean"] = round(float(sqi.mean()), 3)

    except Exception as e:
        result["error"]     = f"{type(e).__name__}: {str(e)[:300]}"
        result["traceback"] = traceback.format_exc()[-800:]

    return result


def main():
    results = []

    for opt_file in sorted(REVO60_DIR.glob("*.opt")):
        print(f"[REVO 60   11.5.3] {opt_file.name}")
        results.append(process_file(opt_file, "REVO 60", "11.5.3"))

    for opt_file in sorted(REVO130_DIR.glob("*.opt")):
        print(f"[REVO FC130 11.5.0] {opt_file.name}")
        results.append(process_file(opt_file, "REVO FC130", "11.5.0"))

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "filename", "device", "soct_version", "file_size_mb",
        "success", "magic_bytes_ok", "n_frames", "bscan_shape", "scan_type_params",
        "chunks_found", "unknown_chunks",
        "cmt_um", "etdrs_present", "rnfl_present", "biometry_present",
        "cdr", "sqi_mean", "study_uid", "error",
    ]
    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    total        = len(results)
    success      = sum(1 for r in results if r["success"])
    revo60_ok    = sum(1 for r in results if r["success"] and r["device"] == "REVO 60")
    revo60_total = sum(1 for r in results if r["device"] == "REVO 60")
    revo130_ok   = sum(1 for r in results if r["success"] and r["device"] == "REVO FC130")
    revo130_total= sum(1 for r in results if r["device"] == "REVO FC130")

    print(f"\n{'='*65}")
    print("RESUMEN SITE B — QUERÉTARO (cross-version / cross-model)")
    print(f"{'='*65}")
    print(f"Total archivos procesados : {total}")
    pct = 100 * success / total if total else 0
    print(f"Éxito global             : {success}/{total} ({pct:.1f}%)")
    print(f"REVO 60    SOCT 11.5.3   : {revo60_ok}/{revo60_total}")
    print(f"REVO FC130 SOCT 11.5.0   : {revo130_ok}/{revo130_total}")

    # CMT fuera de rango
    cmt_values = [r["cmt_um"] for r in results if r["cmt_um"] is not None]
    if cmt_values:
        print(f"\nCMT extraídos ({len(cmt_values)} archivos):")
        for r in results:
            if r["cmt_um"] is not None:
                flag = " ⚠️ fuera de rango" if not (150 <= r["cmt_um"] <= 450) else ""
                print(f"  {r['filename'][:50]:50s}  {r['cmt_um']} µm{flag}")

    # Errores
    failed = [r for r in results if not r["success"]]
    if failed:
        print(f"\nARCHIVOS FALLIDOS ({len(failed)}):")
        for r in failed:
            print(f"  {r['filename']}")
            print(f"    {r['error']}")

    # Chunks desconocidos
    all_unknown: set[str] = set()
    for r in results:
        if r.get("unknown_chunks"):
            for c in r["unknown_chunks"].split("|"):
                if c:
                    all_unknown.add(c)
    if all_unknown:
        print("\n⚠️  CHUNKS DESCONOCIDOS (potenciales chunks nuevos en 11.5.x):")
        for c in sorted(all_unknown):
            files = [r["filename"] for r in results if c in r.get("unknown_chunks", "")]
            print(f"  {c:20s} — en {len(files)} archivo(s): {', '.join(files[:3])}")
    else:
        print("\n✅ Todos los chunks reconocidos por el parser actual")

    # Sample CSV
    print(f"\nCSV: {OUTPUT_CSV}")
    print("\nSample (primeras 4 filas):")
    print(f"  {'filename':<45} {'device':<12} {'ok'} {'frames':>6} {'cmt_um':>7} {'error'}")
    print(f"  {'-'*45} {'-'*12} {'-'} {'-'*6} {'-'*7} {'-'*30}")
    for r in results[:4]:
        print(
            f"  {r['filename'][:45]:<45} {r['device']:<12} "
            f"{'✓' if r['success'] else '✗'} "
            f"{str(r['n_frames'] or ''):>6} "
            f"{str(r['cmt_um'] or ''):>7} "
            f"{(r['error'] or '')[:40]}"
        )


if __name__ == "__main__":
    main()
