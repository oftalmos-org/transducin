#!/usr/bin/env python3
"""
Renders a real Transducin-generated SR TID 1500 .dcm file as a figure for
the paper. Two outputs:

  - <text-out>.txt : FULL literal content tree (every item, every UID) --
    useful to sanity-check the SR, not meant to be used as-is in a figure.
  - <out>.png       : a CURATED, compact panel -- header context + Finding
    Site/Laterality + measurements, with repeated measurement families
    (ETDRS-*, mRNFL-*, mGCIPL-*, etc., identified by the Tracking Identifier
    prefix before the first '-') collapsed into one summary line
    ("9 values, 300.4-373.3 um") instead of listing every sector. UIDs are
    never shown in the PNG. Numbers are rounded to 1 decimal.

RUN LOCALLY. Point it at any SR .dcm Transducin already generated. Do not
paste .dcm content into chat -- only the output PNG (visual, safe to
share) and the text tree (useful to sanity-check before sharing the PNG).

Usage:
    python render_sr_figure.py path\to\some_SR.dcm [--out sr_figure.png] [--max-groups 12]
"""

import sys
import argparse
from collections import defaultdict
from pathlib import Path

import pydicom
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _num_item(item):
    """Extract (name, value, unit, tracking_id, finding_site, laterality, modifier) for a NUM item."""
    name = ""
    cn = item.get("ConceptNameCodeSequence")
    if cn:
        name = cn[0].get("CodeMeaning", "")

    value, unit = None, ""
    mv = item.get("MeasuredValueSequence")
    if mv:
        value = mv[0].get("NumericValue")
        unit_seq = mv[0].get("MeasurementUnitsCodeSequence")
        unit = unit_seq[0].get("CodeValue", "") if unit_seq else ""

    tracking_id, finding_site, laterality, modifier = "", "", "", ""
    for child in item.get("ContentSequence", []):
        cn2 = child.get("ConceptNameCodeSequence")
        meaning = cn2[0].get("CodeMeaning", "") if cn2 else ""
        if child.get("ValueType") == "TEXT" and meaning == "Tracking Identifier":
            tracking_id = child.get("TextValue", "")
        elif meaning == "Finding Site":
            cc = child.get("ConceptCodeSequence")
            finding_site = cc[0].get("CodeMeaning", "") if cc else ""
            for gc in child.get("ContentSequence", []):
                gcn = gc.get("ConceptNameCodeSequence")
                gmeaning = gcn[0].get("CodeMeaning", "") if gcn else ""
                gcc = gc.get("ConceptCodeSequence")
                gval = gcc[0].get("CodeMeaning", "") if gcc else ""
                if gmeaning == "Laterality":
                    laterality = gval
                elif gmeaning == "Topographical modifier":
                    modifier = gval

    return {
        "name": name,
        "value": value,
        "unit": unit,
        "tracking_id": tracking_id,
        "finding_site": finding_site,
        "laterality": laterality,
        "modifier": modifier,
    }


def walk_full(seq, depth=0, lines=None):
    """Full literal text tree -- for the .txt output only."""
    if lines is None:
        lines = []
    for item in seq:
        cn = item.get("ConceptNameCodeSequence")
        name = cn[0].get("CodeMeaning", "") if cn else ""
        vt = item.get("ValueType", "")
        value = ""
        if vt == "TEXT":
            value = item.get("TextValue", "")
        elif vt == "CODE":
            cc = item.get("ConceptCodeSequence")
            value = cc[0].get("CodeMeaning", "") if cc else ""
        elif vt == "NUM":
            mv = item.get("MeasuredValueSequence")
            if mv:
                num = mv[0].get("NumericValue", "")
                unit_seq = mv[0].get("MeasurementUnitsCodeSequence")
                unit = unit_seq[0].get("CodeValue", "") if unit_seq else ""
                value = f"{num} {unit}".strip()
        elif vt == "UIDREF":
            value = item.get("UID", "")
        label = name if name else vt
        if value:
            label = f"{label}: {value}"
        lines.append("  " * depth + "- " + (label or vt or "(item)"))
        child = item.get("ContentSequence")
        if child:
            walk_full(child, depth + 1, lines)
    return lines


def collect_num_items(seq, out=None):
    """Recursively collect all NUM-type items (the actual measurements)."""
    if out is None:
        out = []
    for item in seq:
        if item.get("ValueType") == "NUM":
            out.append(_num_item(item))
        child = item.get("ContentSequence")
        if child:
            collect_num_items(child, out)
    return out


def build_compact_lines(header_lines, num_items, max_groups=12):
    """Group repeated measurement families (by tracking-id prefix before '-')
    into one summary line each; keep singletons expanded."""
    families = defaultdict(list)
    for it in num_items:
        prefix = it["tracking_id"].split("-")[0] if it["tracking_id"] else it["name"]
        families[prefix].append(it)

    lines = list(header_lines)
    n_shown = 0
    for prefix, items in families.items():
        if n_shown >= max_groups:
            lines.append(f"... {len(families) - n_shown} more measurement groups omitted for clarity")
            break
        rep = items[0]
        if len(items) == 1:
            display_name = "Central Macular Thickness (CMT)" if rep["tracking_id"] == "CMT" else rep["name"]
            loc = ", ".join(x for x in [rep["finding_site"], rep["laterality"]] if x)
            v = f"{rep['value']:.1f}" if isinstance(rep["value"], (int, float)) else rep["value"]
            unit = "" if rep["unit"] == "1" else f" {rep['unit']}"
            lines.append(f"{display_name} = {v}{unit}  [{loc}]")
        else:
            # If the group spans more than one finding site (e.g. ETDRS grid:
            # 1 foveal center + 8 peripheral sectors), use the broader parent
            # site rather than the unrepresentative first item's site.
            distinct_sites = {x["finding_site"] for x in items if x["finding_site"]}
            loc_site = distinct_sites.pop() if len(distinct_sites) == 1 else "Retina"
            loc = ", ".join(x for x in [loc_site, rep["laterality"]] if x)
            vals = [x["value"] for x in items if isinstance(x["value"], (int, float))]
            vmin, vmax = (min(vals), max(vals)) if vals else (None, None)
            unit = "" if rep["unit"] == "1" else f" {rep['unit']}"
            lines.append(f"{rep['name']} ({prefix}-*): {len(items)} sectors, " f"{vmin:.1f}-{vmax:.1f}{unit}  [{loc}]")
        n_shown += 1
    return lines


# Hockney-inspired palette: pool cerulean, coral, sunny yellow, grass green
CERULEAN_DARK = "#00728A"
CORAL_DARK = "#B84A2C"
YELLOW_DARK = "#8A6A1F"
GREEN_DARK = "#3F7D5C"
HEADER_COLOR = CERULEAN_DARK

_FAMILY_COLORS = [CORAL_DARK, YELLOW_DARK, GREEN_DARK, CERULEAN_DARK]


def _line_color(line, family_color_map):
    if line.startswith("["):
        return HEADER_COLOR
    if not line.strip():
        return "#1a1a1a"
    # Assign a stable color per measurement family (text before " = " or " (")
    key = line.split(" = ")[0].split(" (")[0].strip()
    if key not in family_color_map:
        family_color_map[key] = _FAMILY_COLORS[len(family_color_map) % len(_FAMILY_COLORS)]
    return family_color_map[key]


def render_png(lines, out_path, title):
    fig_h = max(3, 0.32 * len(lines) + 1.2)
    fig, ax = plt.subplots(figsize=(8.5, fig_h))
    fig.patch.set_facecolor("#FBFAF6")
    ax.set_facecolor("#FBFAF6")
    ax.axis("off")
    ax.set_title(title, fontsize=12, fontweight="bold", loc="left", color=CERULEAN_DARK)

    family_color_map = {}
    y = 1.0
    step = 1.0 / (len(lines) + 1)
    for line in lines:
        indent = 0.0 if line.startswith("[") or line == "" else 0.02
        color = _line_color(line, family_color_map)
        weight = "bold" if line.startswith("[") else "normal"
        ax.text(
            indent,
            y,
            line,
            fontsize=9.5,
            family="monospace",
            va="top",
            color=color,
            fontweight=weight,
            transform=ax.transAxes,
        )
        y -= step

    plt.tight_layout()
    fig.savefig(out_path, dpi=300, bbox_inches="tight", facecolor="#FBFAF6")
    fig.savefig(Path(out_path).with_suffix(".svg"), bbox_inches="tight", facecolor="#FBFAF6")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sr_path", help="Path to a Transducin-generated SR .dcm file")
    ap.add_argument("--out", default="sr_figure.png", help="Output PNG path")
    ap.add_argument("--text-out", default="sr_figure.txt", help="Output text tree path")
    ap.add_argument("--max-groups", type=int, default=12, help="Max measurement groups shown in PNG")
    args = ap.parse_args()

    sr_path = Path(args.sr_path)
    if not sr_path.exists():
        print(f"ERROR: {sr_path} not found.")
        sys.exit(1)

    ds = pydicom.dcmread(str(sr_path))
    sop_class = str(getattr(ds, "SOPClassUID", ""))
    print(f"SOPClassUID: {sop_class}")
    print(f"Modality: {getattr(ds, 'Modality', '?')}")

    content = ds.get("ContentSequence")
    if not content:
        print("ERROR: no ContentSequence found -- is this really an SR?")
        sys.exit(1)

    # Full literal tree -> text file only
    full_lines = walk_full(content)
    text_report = "\n".join(full_lines)
    text_out = Path(args.text_out)
    text_out.write_text(text_report, encoding="utf-8")
    print(f"Escrito (completo, para revisar): {text_out}")

    # Compact curated panel -> PNG
    device_name = getattr(ds, "ManufacturerModelName", "") or ""
    header = [
        "[SOPClassUID] Comprehensive SR (TID 1500 Measurement Report)",
        f"[Device] {device_name}" if device_name else "",
        "",
    ]
    header = [h for h in header if h]

    num_items = collect_num_items(content)
    compact = build_compact_lines(header, num_items, max_groups=args.max_groups)
    print("\n".join(compact))

    title = "(B)  Transducin-generated SR TID 1500 -- measurement summary"
    render_png(compact, args.out, title)
    print(f"Escrito (figura curada): {args.out}")


if __name__ == "__main__":
    main()
