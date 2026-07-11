#!/usr/bin/env python3
"""
check_gclipl_rescue.py -- for the "top_or_bm_missing" (macular) /
"nfl_or_top_missing" (optic_nerve) bucket found by diagnose_cmt_geometry.py,
checks whether GCL/INL are present and would produce a valid mGCIPL value
via compute_gcl_ipl() -- which would let those files still pass SR TID 1500
via the mGCIPL SNOMED code even though CMT/RNFL/CDR are unavailable.

No filenames or patient identifiers are ever read or printed -- only
site/device/soct_version grouping and aggregate counts.

Usage:
    python check_gclipl_rescue.py [CORPUS_ROOT]

Produces (next to this script):
    gclipl_rescue_check.txt -- aggregate only, safe to paste into chat.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from transducin.opt_extractor import extract_from_opt, build_noel_index
from transducin.revo_opt_reader import (
    parse_opt_chunks,
    parse_octparams,
    extract_layer,
    _extract_layer_with_fallback,
    extract_sqi,
    compute_gcl_ipl,
)

from corpus_audit import classify_path, DEFAULT_CORPUS_ROOT

OUT_TXT = Path(__file__).parent / "gclipl_rescue_check.txt"


def top_or_bm_missing(data, chunks):
    top = extract_layer(data, chunks, "TOP")
    bm = _extract_layer_with_fallback(data, chunks, "BM", "BOTTOM")
    return top is None or bm is None


def nfl_or_top_missing(data, chunks):
    nfl = extract_layer(data, chunks, "NFL")
    top = extract_layer(data, chunks, "TOP")
    return nfl is None or top is None


def main():
    corpus_root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CORPUS_ROOT
    files = sorted(corpus_root.rglob("*.opt"))
    total = len(files)
    print(f"Analizando {total} archivos bajo {corpus_root}...")

    print("Construyendo NOEL index...")
    noel_index = build_noel_index(corpus_root)

    macular_results = Counter()
    macular_by_group = {}
    optic_results = Counter()
    optic_by_group = {}

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
            params = parse_octparams(data, chunks)
        except Exception:
            continue

        if cd.study_type == "macular":
            if not top_or_bm_missing(data, chunks):
                continue  # only interested in the affected subset
            gcl = extract_layer(data, chunks, "GCL")
            inl = extract_layer(data, chunks, "INL")
            if gcl is None or inl is None:
                bucket = "gcl_or_inl_also_missing"
            elif gcl.shape != inl.shape:
                bucket = "gcl_inl_shape_mismatch"
            else:
                sqi = extract_sqi(data, chunks)
                gclipl = compute_gcl_ipl(gcl, inl, params, laterality=cd.laterality or "R", sqi=sqi)
                bucket = (
                    "gclipl_would_rescue"
                    if (gclipl is not None and gclipl.has_data())
                    else "gcl_inl_present_but_gclipl_still_none"
                )
            macular_results[bucket] += 1
            macular_by_group.setdefault(group, Counter())[bucket] += 1
        else:
            if not nfl_or_top_missing(data, chunks):
                continue
            gcl = extract_layer(data, chunks, "GCL")
            inl = extract_layer(data, chunks, "INL")
            if gcl is None or inl is None:
                bucket = "gcl_or_inl_also_missing"
            elif gcl.shape != inl.shape:
                bucket = "gcl_inl_shape_mismatch"
            else:
                sqi = extract_sqi(data, chunks)
                gclipl = compute_gcl_ipl(gcl, inl, params, laterality=cd.laterality or "R", sqi=sqi)
                bucket = (
                    "gclipl_would_rescue"
                    if (gclipl is not None and gclipl.has_data())
                    else "gcl_inl_present_but_gclipl_still_none"
                )
            optic_results[bucket] += 1
            optic_by_group.setdefault(group, Counter())[bucket] += 1

        if idx % 50 == 0 or idx == total:
            print(f"  ... {idx}/{total}")

    lines = ["=" * 70, "GCL/INL rescue check -- para archivos con TOP/BM/NFL ausentes", "=" * 70, ""]
    lines.append("-- MACULAR (subset con top_or_bm_missing) --")
    for b, n in macular_results.most_common():
        lines.append(f"  {n:>4}  {b}")
    lines.append("\n-- MACULAR por grupo --")
    for g in sorted(macular_by_group):
        lines.append(f"\n  {g}:")
        for b, n in macular_by_group[g].most_common():
            lines.append(f"    {n:>4}  {b}")

    lines.append("\n" + "=" * 70)
    lines.append("-- OPTIC_NERVE (subset con nfl_or_top_missing) --")
    for b, n in optic_results.most_common():
        lines.append(f"  {n:>4}  {b}")
    lines.append("\n-- OPTIC_NERVE por grupo --")
    for g in sorted(optic_by_group):
        lines.append(f"\n  {g}:")
        for b, n in optic_by_group[g].most_common():
            lines.append(f"    {n:>4}  {b}")

    report = "\n".join(lines)
    print("\n" + report)
    OUT_TXT.write_text(report, encoding="utf-8")
    print(f"\nEscrito: {OUT_TXT}")


if __name__ == "__main__":
    main()
