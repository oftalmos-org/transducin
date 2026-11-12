#!/usr/bin/env python3
"""Visor HTML autocontenido de B-scans y contornos de capa de un .e2e de Spectralis (ADR-027).

Uso:
    python scripts/e2e_contour_viewer.py ARCHIVO.e2e [-o salida.html] [--volume N ...]

Dibuja cada corte B-scan con los contornos de capa (``contourN``) encima, para comprobar a
simple vista a qué capa anatómica corresponde cada ID (p. ej. si el contorno 1 sigue la
membrana de Bruch). Las coordenadas son las nativas del archivo; no se modifican.

PRIVACIDAD: el HTML incrusta los píxeles del estudio, así que es un dato identificable.
- Solo se ejecuta en local; no sube nada a ningún servicio.
- Se niega a escribir dentro del repositorio; por defecto escribe junto al .e2e (corpus local
  fuera de Git, $TRANSDUCIN_REAL_DATA_DIR).
- No se leen ni se incrustan nombre, fecha de nacimiento, identificadores ni el nombre del
  archivo de origen.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from transducin.readers.e2e_reader import E2EReader

TOOL_VERSION = "0.1"
PALETTE = ["#4da3ff", "#ff5a5f", "#ffcc66", "#5be37d", "#c792ea", "#4dd0e1", "#ff9e64", "#a3be8c", "#e5c07b", "#d19a66"]

TEMPLATE = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visor de contornos E2E</title>
<style>
:root{--bg:#111418;--panel:#1a1f26;--ink:#e6e9ee;--mute:#9aa4b2;--line:#2c343f;--acc:#4da3ff;--warn:#ffcc66}
@media (prefers-color-scheme: light){:root{--bg:#f4f6f9;--panel:#fff;--ink:#16202b;--mute:#5a6675;--line:#d5dbe3;--acc:#0a63c9;--warn:#8a5a00}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.4 system-ui,sans-serif}
#app{display:grid;grid-template-columns:minmax(280px,340px) 1fr;height:100vh}
aside{background:var(--panel);border-right:1px solid var(--line);overflow:auto;padding:12px 14px}
h1{font-size:15px;margin:0 0 4px}h2{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute);margin:14px 0 6px}
.note{color:var(--warn);font-size:12px;border:1px solid var(--line);border-radius:6px;padding:6px 8px;margin:6px 0}
.row{display:flex;align-items:center;gap:8px;margin:4px 0}input[type=range]{flex:1}select{width:100%;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:4px;padding:3px 5px}
.sw{width:12px;height:12px;border-radius:3px;display:inline-block}.mono{font-family:ui-monospace,monospace}.mute{color:var(--mute)}
main{position:relative;min-width:0;background:#000}canvas{display:block;width:100%;height:100%}
#readout{position:absolute;left:10px;bottom:10px;background:#000a;color:#fff;border-radius:6px;padding:5px 8px;font:12px ui-monospace,monospace}
@media (max-width:760px){#app{grid-template-columns:1fr;grid-template-rows:auto 60vh}aside{border-right:0;border-bottom:1px solid var(--line);max-height:48vh}}
</style></head><body>
<div id="app"><aside>
<h1>Contornos de capa — Spectralis E2E</h1>
<div class="note">Contiene píxeles del estudio: dato identificable. Guárdalo fuera de Git. Herramienta v__VER__.</div>
<h2>Volumen</h2><select id="vol"></select>
<h2>Corte</h2><div class="row"><input id="sl" type="range" min="0" value="0"><span id="slv" class="mono"></span></div>
<h2>Contornos</h2><div id="legend"></div>
<div class="note">Profundidad media = promedio de la fila (px) en todo el volumen: la capa más profunda
suele ser la membrana de Bruch. Los IDs no están documentados por el fabricante; esto es para comprobarlos.</div>
</aside><main><canvas id="cv"></canvas><div id="readout"></div></main></div>
<script>
const DATA = __DATA__;
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const volSel = document.getElementById('vol'), sl = document.getElementById('sl'), slv = document.getElementById('slv');
const legend = document.getElementById('legend'), readout = document.getElementById('readout');
let cur = 0, idx = 0, imgs = {}, on = {};
DATA.volumes.forEach((v, i) => { const o = document.createElement('option'); o.value = i; o.textContent = `Volumen ${i+1} · ojo ${v.laterality||'?'} · ${v.n_slices} cortes`; volSel.appendChild(o); });
function buildLegend() {
  legend.innerHTML = ''; const v = DATA.volumes[cur];
  v.contours.forEach(c => {
    if (!(c.key in on)) on[c.key] = true;
    const d = document.createElement('div'); d.className = 'row';
    d.innerHTML = `<input type="checkbox" ${on[c.key]?'checked':''} id="c_${c.key}"><span class="sw" style="background:${c.color}"></span><span class="mono">${c.key}</span><span class="mute mono">prof. media ${c.mean_depth.toFixed(1)} px</span>`;
    d.querySelector('input').onchange = e => { on[c.key] = e.target.checked; draw(); };
    legend.appendChild(d);
  });
}
function load(i) { const v = DATA.volumes[cur]; if (!imgs[cur]) imgs[cur] = {}; if (imgs[cur][i] !== undefined) return Promise.resolve(imgs[cur][i]);
  const src = v.images[i]; if (!src) { imgs[cur][i] = null; return Promise.resolve(null); }
  return new Promise(r => { const im = new Image(); im.onload = () => { imgs[cur][i] = im; r(im); }; im.src = 'data:image/png;base64,' + src; }); }
async function draw() {
  const v = DATA.volumes[cur]; const im = await load(idx); const r = cv.getBoundingClientRect();
  cv.width = r.width * devicePixelRatio; cv.height = r.height * devicePixelRatio; ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);
  ctx.fillStyle = '#000'; ctx.fillRect(0,0,r.width,r.height); if (!im) { readout.textContent = 'Sin imagen en este corte'; return; }
  const s = Math.min(r.width/im.width, r.height/im.height), ox = (r.width-im.width*s)/2, oy = (r.height-im.height*s)/2;
  ctx.imageSmoothingEnabled = true; ctx.drawImage(im, ox, oy, im.width*s, im.height*s);
  v.contours.forEach(c => { if (!on[c.key]) return; const a = c.slices[idx]; if (!a) return;
    ctx.beginPath(); ctx.strokeStyle = c.color; ctx.lineWidth = 1.6; let pen = false;
    a.forEach((y, k) => { if (y === null) { pen = false; return; } const X = ox + (k + 0.5) * (im.width/a.length) * s, Y = oy + y * s;
      if (!pen) { ctx.moveTo(X, Y); pen = true; } else ctx.lineTo(X, Y); }); ctx.stroke(); });
  readout.textContent = `corte ${idx+1}/${v.n_slices} · imagen ${im.width}×${im.height}`;
}
function setVol(i) { cur = +i; idx = Math.floor(DATA.volumes[cur].n_slices/2); sl.max = DATA.volumes[cur].n_slices-1; sl.value = idx; slv.textContent = idx+1; buildLegend(); draw(); }
volSel.onchange = e => setVol(e.target.value); sl.oninput = e => { idx = +e.target.value; slv.textContent = idx+1; draw(); };
window.onresize = draw; setVol(0);
</script></body></html>
"""


def _png_b64(a: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(a, mode="L").save(buf, format="PNG", optimize=False)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _contour_json(arr) -> list | None:
    if arr is None:
        return None
    return [None if not np.isfinite(v) else round(float(v), 1) for v in arr]


def build_data(path: Path, only: list[int] | None) -> dict:
    reader = E2EReader(path)
    layers = {v.volume_id: v for v in reader.read_layer_volumes()}
    out = []
    for n, bv in enumerate(reader.read_bscans(), 1):
        if only and n not in only:
            continue
        lv = layers.get(bv.volume_id)
        contours = []
        if lv is not None:
            for i, key in enumerate(sorted(lv.contours, key=lambda k: int(k.replace("contour", "")))):
                slices = [_contour_json(a) for a in lv.contours[key]]
                vals = [v for s in slices if s for v in s if v is not None]
                if not vals:
                    continue
                contours.append(
                    {
                        "key": key,
                        "color": PALETTE[i % len(PALETTE)],
                        "mean_depth": float(np.mean(vals)),
                        "slices": slices + [None] * (len(bv.images) - len(slices)),
                    }
                )
        out.append(
            {
                "laterality": bv.laterality,
                "n_slices": len(bv.images),
                "images": [None if a is None else _png_b64(a) for a in bv.images],
                "contours": contours,
            }
        )
    return {"tool_version": TOOL_VERSION, "volumes": out}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("e2e", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=None)
    ap.add_argument("--volume", type=int, nargs="*", help="Números de volumen a incluir (1..N); por defecto todos")
    args = ap.parse_args()

    src = args.e2e.expanduser().resolve()
    if not src.exists():
        print("Archivo no encontrado.", file=sys.stderr)
        return 2
    out = (args.output or src.with_suffix(".contornos.html")).expanduser().resolve()
    if REPO_ROOT == out or REPO_ROOT in out.parents:
        print(
            "Rechazado: el HTML contiene píxeles del estudio y no puede escribirse dentro del repositorio.",
            file=sys.stderr,
        )
        return 2

    data = build_data(src, args.volume)
    if not data["volumes"]:
        print("El archivo no trae cortes B-scan.", file=sys.stderr)
        return 1
    html = TEMPLATE.replace("__VER__", TOOL_VERSION).replace("__DATA__", json.dumps(data, separators=(",", ":")))
    out.write_text(html, encoding="utf-8")
    print(f"Escrito {out.name}: {len(data['volumes'])} volúmenes, {out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
