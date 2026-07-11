#!/usr/bin/env python3
"""
diagnose_cmt_geometry.py -- corpus-wide root-cause breakdown for the
"no_measurement_cmt_patch_invalido" / "no_measurement_rnfl_capas_invalidas"
buckets found by corpus_audit.py.

Both compute_cmt() and compute_peripapillary_rnfl() (transducin/revo_opt_reader.py)
return None when zero pixels fall inside a small geometric region (a 500um-radius
circle for CMT, a 1600-1800um ring for peripapillary RNFL) AFTER validity
filtering. This script replicates that internal geometry -- without modifying
revo_opt_reader.py -- and classifies every macular/optic_nerve file into
exactly one bucket, to distinguish:

  - a true data problem (segmentation layers missing/mismatched shape)
  - SQI filtering wiping out the relevant region (real signal-quality issue)
  - the region itself (as currently computed) landing on out-of-range/empty
    segmentation values with good SQI (suggests the *assumed center* is
    wrong, not the data itself)

Buckets (macular / compute_cmt):
  ok                    -- compute_cmt would succeed
  top_or_bm_missing      -- TOP or BM(+BOTTOM fallback) chunk absent
  shape_mismatch          -- TOP and BM arrays have different (n_bscans, n_ascans)
  sqi_zeroed_center       -- central circle had valid segmentation before SQI
                             masking, but SQI<0.5 zeroed all of it
  center_out_of_range     -- central circle segmentation is <=0 or >=800um
                             even BEFORE SQI masking (not an SQI issue)

Buckets (optic_nerve / compute_peripapillary_rnfl, global sector only):
  ok
  nfl_or_top_missing
  shape_mismatch
  dmarkers_absent_geometric_fallback  -- no DMARKERS chunk, ring centered on
                                          geometric center of the volume
  ring_empty_with_dmarkers            -- DMARKERS WAS present (real disc
                                          center used) but ring still empty

No filenames or patient identifiers are ever read or printed -- only
site/device/soct_version (same folder-derived grouping as corpus_audit.py)
and aggregate counts.

Usage:
    python diagnose_cmt_geometry.py [CORPUS_ROOT]

Produces (next to this script):
    cmt_geometry_diagnosis.txt -- aggregate only, safe to paste into chat.
"""

import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from transducin.opt_extractor import extract_from_opt, build_noel_index
from transducin.revo_opt_reader import (
    parse_opt_chunks,
    parse_octparams,
    extract_layer,
    _extract_layer_with_fallback,
    extract_sqi,
    _apply_sqi_mask,
    extract_disc_center,
    _CMT_RADIUS_UM,
)

from corpus_audit import classify_path, DEFAULT_CORPUS_ROOT

OUT_TXT = Path(__file__).parent / "cmt_geometry_diagnosis.txt"

_RING_MIN_UM = 1700.0 - 200.0 / 2
_RING_MAX_UM = 1700.0 + 200.0 / 2


def diagnose_macular(data, chunks, params):
    top = extract_layer(data, chunks, "TOP")
    bm = _extract_layer_with_fallback(data, chunks, "BM", "BOTTOM")
    if top is None or bm is None:
        return "top_or_bm_missing"
    if top.shape != bm.shape:
        return "shape_mismatch"

    axial_um = params["axial_um"]
    lateral_um = params["lateral_um"]
    n_b, n_a = top.shape
    bscan_um = params["scan_width_mm"] * 1000.0 / n_b

    thickness_raw = (bm - top) * axial_um
    sqi = extract_sqi(data, chunks)
    thickness_masked = _apply_sqi_mask(thickness_raw, sqi)

    b0, a0 = n_b // 2, n_a // 2
    db = (np.arange(n_b) - b0) * bscan_um
    da = (np.arange(n_a) - a0) * lateral_um
    B, A = np.meshgrid(db, da, indexing="ij")
    R = np.sqrt(B**2 + A**2)
    center = R < _CMT_RADIUS_UM

    valid_before = center & (thickness_raw > 0) & (thickness_raw < 800)
    valid_after = center & (thickness_masked > 0) & (thickness_masked < 800)

    if valid_after.any():
        return "ok"
    if valid_before.any():
        return "sqi_zeroed_center"
    return "center_out_of_range"


def diagnose_optic_nerve(data, chunks, params):
    nfl = extract_layer(data, chunks, "NFL")
    top = extract_layer(data, chunks, "TOP")
    if nfl is None or top is None:
        return "nfl_or_top_missing"
    if nfl.shape != top.shape:
        return "shape_mismatch"

    n_b, n_a = nfl.shape
    axial_um = params["axial_um"]
    lateral_um = params["lateral_um"]
    bscan_um = params["scan_width_mm"] * 1000.0 / n_b

    b_center, a_center = extract_disc_center(data, chunks, params)
    has_dmarkers = "DMARKERS" in chunks

    thickness = (nfl - top) * axial_um
    db = (np.arange(n_b) - b_center) * bscan_um
    da = (np.arange(n_a) - a_center) * lateral_um
    B, A = np.meshgrid(db, da, indexing="ij")
    R = np.sqrt(B**2 + A**2)
    ring = (R >= _RING_MIN_UM) & (R < _RING_MAX_UM)
    valid = ring & (thickness > 0) & (thickness < 250)

    if int(valid.sum()) >= 5:
        return "ok"
    if not has_dmarkers:
        return "dmarkers_absent_geometric_fallback"
    return "ring_empty_with_dmarkers"


def main():
    corpus_root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CORPUS_ROOT
    files = sorted(corpus_root.rglob("*.opt"))
    total = len(files)
    print(f"Analizando {total} archivos bajo {corpus_root}...")

    print("Construyendo NOEL index...")
    noel_index = build_noel_index(corpus_root)

    macular_buckets = Counter()
    macular_by_group = {}
    optic_buckets = Counter()
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
            bucket = diagnose_macular(data, chunks, params)
            macular_buckets[bucket] += 1
            macular_by_group.setdefault(group, Counter())[bucket] += 1
        else:
            bucket = diagnose_optic_nerve(data, chunks, params)
            optic_buckets[bucket] += 1
            optic_by_group.setdefault(group, Counter())[bucket] += 1

        if idx % 25 == 0 or idx == total:
            print(f"  ... {idx}/{total}")

    lines = ["=" * 70, "CMT / RNFL geometry root-cause diagnosis", "=" * 70, ""]
    lines.append("-- MACULAR (compute_cmt) buckets, total --")
    for b, n in macular_buckets.most_common():
        lines.append(f"  {n:>4}  {b}")
    lines.append("\n-- MACULAR buckets por grupo --")
    for g in sorted(macular_by_group):
        lines.append(f"\n  {g}:")
        for b, n in macular_by_group[g].most_common():
            lines.append(f"    {n:>4}  {b}")

    lines.append("\n" + "=" * 70)
    lines.append("-- OPTIC_NERVE (compute_peripapillary_rnfl) buckets, total --")
    for b, n in optic_buckets.most_common():
        lines.append(f"  {n:>4}  {b}")
    lines.append("\n-- OPTIC_NERVE buckets por grupo --")
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
