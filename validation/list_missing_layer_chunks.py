#!/usr/bin/env python3
"""
list_missing_layer_chunks.py -- for macular/optic_nerve files where TOP or
BM(+BOTTOM fallback)/NFL is absent (the "top_or_bm_missing" /
"nfl_or_top_missing" buckets from diagnose_cmt_geometry.py), lists which
OTHER chunk names ARE present in the file.

Purpose: check whether there's an alternate segmentation-layer chunk name
Transducin isn't trying yet (the same kind of fix already applied for
BM -> BOTTOM fallback), or whether the chunk is genuinely absent (on-device
segmentation never ran/saved for that scan).

No filenames or patient identifiers are ever read or printed -- only
site/device/soct_version grouping and aggregate chunk-name counts.

Usage:
    python list_missing_layer_chunks.py [CORPUS_ROOT]

Produces (next to this script):
    missing_layer_chunks.txt -- aggregate only, safe to paste into chat.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from transducin.opt_extractor import extract_from_opt, build_noel_index
from transducin.revo_opt_reader import parse_opt_chunks, extract_layer

from corpus_audit import classify_path, DEFAULT_CORPUS_ROOT

OUT_TXT = Path(__file__).parent / "missing_layer_chunks.txt"

_KNOWN_LAYER_NAMES = {"TOP", "NFL", "GCL", "IPL", "INL", "OPL", "ONL", "ELM", "EZOS", "ISOS", "BM", "BOTTOM"}


def main():
    corpus_root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CORPUS_ROOT
    files = sorted(corpus_root.rglob("*.opt"))
    total = len(files)
    print(f"Analizando {total} archivos bajo {corpus_root}...")

    print("Construyendo NOEL index...")
    noel_index = build_noel_index(corpus_root)

    other_chunks_when_top_missing = Counter()
    other_chunks_when_bm_missing = Counter()
    other_chunks_when_nfl_missing = Counter()
    n_top_missing = 0
    n_bm_missing = 0
    n_nfl_missing = 0
    by_group = {}

    for idx, opt_path in enumerate(files, 1):
        rel_parts = opt_path.relative_to(corpus_root).parent.parts
        site, device, soct = classify_path(rel_parts)
        group = f"{site} {device} {soct}"

        try:
            cd = extract_from_opt(opt_path, noel_index=noel_index)
        except Exception:
            continue

        if cd.study_type not in ("macular", "optic_nerve"):
            continue

        try:
            data = opt_path.read_bytes()
            chunks = parse_opt_chunks(data)
        except Exception:
            continue

        chunk_names = set(chunks.keys())
        extra_names = chunk_names - _KNOWN_LAYER_NAMES

        if cd.study_type == "macular":
            top = extract_layer(data, chunks, "TOP")
            bm_direct = extract_layer(data, chunks, "BM")
            bottom_direct = extract_layer(data, chunks, "BOTTOM")
            bm_missing = (bm_direct is None or not bm_direct.any()) and (
                bottom_direct is None or not bottom_direct.any()
            )
            if top is None:
                n_top_missing += 1
                other_chunks_when_top_missing.update(extra_names)
                by_group.setdefault(group, Counter())["top_missing"] += 1
            if bm_missing:
                n_bm_missing += 1
                other_chunks_when_bm_missing.update(extra_names)
                by_group.setdefault(group, Counter())["bm_and_bottom_missing"] += 1
        else:
            nfl = extract_layer(data, chunks, "NFL")
            top = extract_layer(data, chunks, "TOP")
            if nfl is None:
                by_group.setdefault(group, Counter())["nfl_missing"] += 1
                other_chunks_when_nfl_missing.update(extra_names)
                n_nfl_missing += 1
            if top is None:
                by_group.setdefault(group, Counter())["top_missing_optic_nerve"] += 1

        if idx % 50 == 0 or idx == total:
            print(f"  ... {idx}/{total}")

    lines = ["=" * 70, "Chunks presentes cuando falta TOP/BM/NFL (busca fallback candidato)", "=" * 70, ""]
    lines.append(f"Archivos con TOP ausente (macular): {n_top_missing}")
    lines.append(f"Archivos con BM+BOTTOM ausentes (macular): {n_bm_missing}")
    lines.append(f"Archivos con NFL ausente (optic_nerve): {n_nfl_missing}")

    lines.append("\n-- Otros chunks presentes cuando TOP falta (macular) --")
    for name, n in other_chunks_when_top_missing.most_common(30):
        lines.append(f"  {n:>4}x  {name}")

    lines.append("\n-- Otros chunks presentes cuando BM+BOTTOM faltan (macular) --")
    for name, n in other_chunks_when_bm_missing.most_common(30):
        lines.append(f"  {n:>4}x  {name}")

    lines.append("\n-- Otros chunks presentes cuando NFL falta (optic_nerve) --")
    for name, n in other_chunks_when_nfl_missing.most_common(30):
        lines.append(f"  {n:>4}x  {name}")

    lines.append("\n-- Por grupo --")
    for g in sorted(by_group):
        lines.append(f"\n  {g}:")
        for k, n in by_group[g].most_common():
            lines.append(f"    {n:>4}  {k}")

    report = "\n".join(lines)
    print("\n" + report)
    OUT_TXT.write_text(report, encoding="utf-8")
    print(f"\nEscrito: {OUT_TXT}")


if __name__ == "__main__":
    main()
