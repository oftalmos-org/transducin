#!/usr/bin/env python3
"""Genera un visor HTML autocontenido de los contornos RETINA_1..4 de un .fda de Topcon.

Uso:
    python scripts/topcon_contour_viewer.py ARCHIVO.fda [-o salida.html]

El HTML incrusta los B-scans originales y las coordenadas NATIVAS de los contornos (uint16
crudos de @CONTOUR_INFO). Ojo: los cortes de @IMG_JPEG son flujos JPEG 2000 (firma FF4F FF51),
que los navegadores no decodifican; se decodifican con Pillow y se incrustan como PNG SIN
PÉRDIDA (valores de píxel idénticos a la decodificación). El HTML resultante pesa ~30 MB. Las coordenadas nunca
se modifican: el HTML solo aplica, al dibujar, la transformación que usa FDAReader
(orden de B-scans invertido y y = H - v) y la deja registrada en el JSON exportado.

Las etiquetas nativas (RETINA_n) y las interpretaciones manuales viven en campos separados.
El archivo generado contiene datos del .fda: guárdalo fuera de Git (p. ej. en
$TRANSDUCIN_REAL_DATA_DIR) y respeta las condiciones de la fuente.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import itertools

from transducin.readers import _fda_binary as fb
from transducin.readers.fda_reader import FDAReader

TOOL_VERSION = "0.1"

TEMPLATE = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visor de contornos Topcon FDA</title>
<style>
:root{--bg:#111418;--panel:#1a1f26;--ink:#e6e9ee;--mute:#9aa4b2;--line:#2c343f;--acc:#4da3ff;--warn:#ffcc66}
@media (prefers-color-scheme: light){:root{--bg:#f4f6f9;--panel:#fff;--ink:#16202b;--mute:#5a6675;--line:#d5dbe3;--acc:#0a63c9;--warn:#8a5a00}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.4 system-ui,sans-serif}
#app{display:grid;grid-template-columns:minmax(300px,380px) 1fr;height:100vh}
aside{background:var(--panel);border-right:1px solid var(--line);overflow:auto;padding:12px 14px}
h1{font-size:15px;margin:0 0 4px}h2{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute);margin:14px 0 6px}
.note{color:var(--warn);font-size:12px;border:1px solid var(--line);border-radius:6px;padding:6px 8px;margin:6px 0}
.row{display:flex;align-items:center;gap:8px;margin:4px 0}.row label{min-width:78px;color:var(--mute)}
input[type=range]{flex:1}input[type=text],select{width:100%;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:4px;padding:3px 5px}
button{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:4px;padding:4px 9px;cursor:pointer}button:hover{border-color:var(--acc)}
.card{border:1px solid var(--line);border-radius:6px;padding:7px 8px;margin:6px 0}.card .hd{display:flex;align-items:center;gap:6px;margin-bottom:4px}
.sw{width:12px;height:12px;border-radius:3px;display:inline-block}.mono{font-family:ui-monospace,monospace}.mute{color:var(--mute)}
main{position:relative;min-width:0}canvas{display:block;width:100%;height:100%;cursor:grab}#readout{position:absolute;left:10px;bottom:10px;background:#000a;color:#fff;border-radius:6px;padding:5px 8px;font:12px ui-monospace,monospace;pointer-events:none;max-width:90%}
@media (max-width:760px){#app{grid-template-columns:1fr;grid-template-rows:auto 60vh}aside{border-right:0;border-bottom:1px solid var(--line);max-height:48vh}}
</style></head><body>
<div id="app"><aside>
<h1>Contornos RETINA_1..4 — Topcon FDA</h1>
<div class="mute mono" id="srcinfo"></div>
<div class="note">Las coordenadas nativas <b>no se modifican</b>. Las interpretaciones son manuales o hipótesis y <b>no se usan para cálculos clínicos</b>.</div>
<h2>B-scan</h2>
<div class="row"><label>Corte</label><input type="range" id="slice" min="0" value="0"><span class="mono" id="sliceN" style="min-width:64px"></span></div>
<div class="row"><button id="prev">◀</button><button id="next">▶</button><button id="reset">Restablecer vista</button></div>
<h2>Imagen</h2>
<div class="row"><label>Zoom</label><input type="range" id="zoom" min="0.5" max="8" step="0.05" value="1.6"><span class="mono" id="zoomN" style="min-width:42px"></span></div>
<div class="row"><label>Ventana mín.</label><input type="range" id="lo" min="0" max="254" value="0"></div>
<div class="row"><label>Ventana máx.</label><input type="range" id="hi" min="1" max="255" value="255"></div>
<div class="row"><label>Gamma</label><input type="range" id="gamma" min="0.3" max="2.5" step="0.05" value="1"></div>
<div class="row"><label>Aspecto</label><select id="aspect"><option value="phys">Físico (µm reales)</option><option value="px">Píxeles</option></select></div>
<h2>Contornos (nativos) e interpretación manual</h2>
<div id="cards"></div>
<div class="row"><label>Anotador</label><input type="text" id="annotator" placeholder="iniciales (opcional)"></div>
<div class="row"><button id="export">Exportar JSON</button><button id="importBtn">Importar JSON</button><input type="file" id="importFile" accept="application/json" hidden></div>
<h2>Evidencia medida (resumen)</h2><div id="evidence" class="mute"></div>
<h2>Atajos</h2><div class="mute">←/→ corte · +/− zoom · rueda: zoom · arrastrar: mover · 1–4: mostrar/ocultar contorno · 0: restablecer</div>
</aside><main><canvas id="cv"></canvas><div id="readout">Mueve el cursor sobre la imagen</div></main></div>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const M = D.meta, N = M.n_bscans, W = M.n_ascans, H = M.image_height;
const COLORS = ["#ff5964", "#ffd166", "#06d6a0", "#4da3ff"];
const OPTIONS = [
  ["", [["", "(sin asignar)"]]],
  ["TABS (Yang 2010, 9 fronteras)", ["ILM","NFL/GCL","GCL/IPL","IPL/INL","INL/OPL","ELM","IS/OS","OS/RPE","BM/Choroid"].map(x=>[x,x])],
  ["Nombres RETIMAT / OCT-Converter", ["IZ_RPE","MZ_EZ","RPE","BM"].map(x=>[x,x])],
  ["Otro", [["otra","otra (describir en la nota)"]]],
];
const STATUS = [["hipotesis_provisional","Hipótesis provisional"],["confirmada_visualmente","Confirmada visualmente"],["rechazada","Rechazada"]];
const now = () => new Date().toISOString();
let state = { slice: Math.floor(N/2), zoom: 1.6, tx: 0, ty: 0, lo: 0, hi: 255, gamma: 1, aspect: "phys",
  visible: [true,true,true,true],
  interp: M.native_labels.map((nl,i) => Object.assign({native_label:nl, interpreted_label:"", status:"hipotesis_provisional", note:"", changed_utc:null}, (M.default_interpretations||{})[nl]||{})) };
const cv = document.getElementById("cv"), ctx = cv.getContext("2d");
const off = document.createElement("canvas"); off.width = W; off.height = H; const octx = off.getContext("2d");
const cache = new Map();
function loadImg(s){ if(cache.has(s)) return cache.get(s); const im = new Image(); im.onload = draw; im.src = "data:image/png;base64," + D.images[s]; cache.set(s, im); return im; }
// Coordenadas: se dibuja con la transformación del lector; los valores crudos no se alteran.
function y(contour, s, c){ const row = M.display_transform.bscan_order==="reversed" ? (N-1-s) : s; return H - D.contours[contour][row][c]; }
function geom(){ const sx = state.zoom * (cv.clientWidth / W) * 0.55; const sy = state.aspect==="phys" ? sx * (M.axial_um/M.lateral_um) : sx; return {sx, sy}; }
function fit(){ const {sx,sy} = geom(); state.tx = (cv.clientWidth - W*sx)/2; state.ty = (cv.clientHeight - H*sy)/2; }
function applyLut(im){ octx.drawImage(im,0,0,W,H); const d = octx.getImageData(0,0,W,H), p = d.data, lo = state.lo, hi = Math.max(state.hi, lo+1), g = 1/state.gamma, lut = new Uint8ClampedArray(256);
  for(let v=0;v<256;v++){ lut[v] = 255*Math.pow(Math.min(1,Math.max(0,(v-lo)/(hi-lo))), g); }
  for(let i=0;i<p.length;i+=4){ const v = lut[p[i]]; p[i]=p[i+1]=p[i+2]=v; } octx.putImageData(d,0,0); }
function draw(){
  const dpr = window.devicePixelRatio||1; if(cv.width!==cv.clientWidth*dpr||cv.height!==cv.clientHeight*dpr){ cv.width=cv.clientWidth*dpr; cv.height=cv.clientHeight*dpr; }
  const {sx,sy} = geom(); ctx.setTransform(1,0,0,1,0,0); ctx.clearRect(0,0,cv.width,cv.height); ctx.setTransform(dpr*sx,0,0,dpr*sy,dpr*state.tx,dpr*state.ty);
  const im = loadImg(state.slice); if(im.complete && im.naturalWidth){ applyLut(im); ctx.imageSmoothingEnabled = state.zoom<3; ctx.drawImage(off,0,0); }
  for(let k=0;k<4;k++){ if(!state.visible[k]) continue; ctx.beginPath(); for(let c=0;c<W;c++){ const yy = y(k,state.slice,c)+0.5; c? ctx.lineTo(c+0.5,yy): ctx.moveTo(c+0.5,yy);} ctx.strokeStyle = COLORS[k]; ctx.lineWidth = 1.6/Math.min(sx,sy); ctx.stroke(); }
  document.getElementById("sliceN").textContent = (state.slice+1)+" / "+N; document.getElementById("zoomN").textContent = state.zoom.toFixed(2)+"×";
}
function toImg(ev){ const r = cv.getBoundingClientRect(), {sx,sy} = geom(); return {c: (ev.clientX-r.left-state.tx)/sx, r: (ev.clientY-r.top-state.ty)/sy}; }
cv.addEventListener("mousemove", ev => { const p = toImg(ev), c = Math.round(p.c-0.5), ro = document.getElementById("readout");
  if(c<0||c>=W||p.r<0||p.r>=H){ ro.textContent="fuera de la imagen"; return; }
  let t = "A-scan "+c+" · fila "+Math.round(p.r)+" ("+(p.r*M.axial_um).toFixed(0)+" µm desde arriba)"; let prev = null;
  for(let k=0;k<4;k++){ if(!state.visible[k]) continue; const yy = y(k,state.slice,c); t += "\n"+M.native_labels[k]+": fila "+yy.toFixed(0)+(prev!==null? " (+"+((yy-prev)*M.axial_um).toFixed(0)+" µm)":""); prev = yy; }
  ro.style.whiteSpace="pre"; ro.textContent = t; });
let drag = null;
cv.addEventListener("mousedown", ev => { drag = {x:ev.clientX, y:ev.clientY, tx:state.tx, ty:state.ty}; cv.style.cursor="grabbing"; });
window.addEventListener("mouseup", () => { drag=null; cv.style.cursor="grab"; });
window.addEventListener("mousemove", ev => { if(!drag) return; state.tx = drag.tx + ev.clientX-drag.x; state.ty = drag.ty + ev.clientY-drag.y; draw(); });
cv.addEventListener("wheel", ev => { ev.preventDefault(); const before = toImg(ev), f = ev.deltaY<0?1.12:1/1.12; state.zoom = Math.min(8,Math.max(0.5,state.zoom*f)); const {sx,sy} = geom(), r = cv.getBoundingClientRect();
  state.tx = ev.clientX-r.left-before.c*sx; state.ty = ev.clientY-r.top-before.r*sy; document.getElementById("zoom").value = state.zoom; draw(); }, {passive:false});
function setSlice(s){ state.slice = Math.min(N-1,Math.max(0,s)); document.getElementById("slice").value = state.slice; draw(); }
function reset(){ state.zoom=1.6; document.getElementById("zoom").value=1.6; fit(); draw(); }
document.getElementById("slice").max = N-1; document.getElementById("slice").value = state.slice;
document.getElementById("slice").oninput = e => setSlice(+e.target.value);
document.getElementById("prev").onclick = () => setSlice(state.slice-1); document.getElementById("next").onclick = () => setSlice(state.slice+1);
document.getElementById("reset").onclick = reset;
for(const id of ["lo","hi","gamma"]) document.getElementById(id).oninput = e => { state[id] = +e.target.value; draw(); };
document.getElementById("zoom").oninput = e => { state.zoom = +e.target.value; fit(); draw(); };
document.getElementById("aspect").onchange = e => { state.aspect = e.target.value; fit(); draw(); };
window.addEventListener("keydown", ev => { if(ev.target.tagName==="INPUT"||ev.target.tagName==="SELECT") return;
  if(ev.key==="ArrowLeft") setSlice(state.slice-1); else if(ev.key==="ArrowRight") setSlice(state.slice+1);
  else if(ev.key==="+"||ev.key==="=") { state.zoom=Math.min(8,state.zoom*1.1); fit(); draw(); } else if(ev.key==="-") { state.zoom=Math.max(0.5,state.zoom/1.1); fit(); draw(); }
  else if(ev.key==="0") reset(); else if("1234".includes(ev.key)&&ev.key){ const k=+ev.key-1; state.visible[k]=!state.visible[k]; document.getElementById("vis"+k).checked=state.visible[k]; draw(); } });
window.addEventListener("resize", () => { fit(); draw(); });
// --- tarjetas de contorno ---
const cards = document.getElementById("cards");
function optHtml(sel){ return OPTIONS.map(([g,os]) => (g? '<optgroup label="'+g+'">':'') + os.map(([v,l]) => '<option value="'+v+'"'+(v===sel?" selected":"")+'>'+l+'</option>').join("") + (g?"</optgroup>":"")).join(""); }
function renderCards(){ cards.innerHTML = ""; M.native_labels.forEach((nl,k) => { const it = state.interp[k], div = document.createElement("div"); div.className="card";
  div.innerHTML = '<div class="hd"><input type="checkbox" id="vis'+k+'" '+(state.visible[k]?"checked":"")+'><span class="sw" style="background:'+COLORS[k]+'"></span><b class="mono">'+nl+'</b><span class="mute">(nativo, no editable) · '+M.stats[nl]+'</span></div>'
   + '<div class="row"><label>Interpretación</label><select class="interp">'+optHtml(it.interpreted_label)+'</select></div>'
   + '<div class="row"><label>Estado</label><select class="status">'+STATUS.map(([v,l])=>'<option value="'+v+'"'+(v===it.status?" selected":"")+'>'+l+'</option>').join("")+'</select></div>'
   + '<div class="row"><label>Nota</label><input type="text" class="nt" value="'+(it.note||"").replace(/"/g,"&quot;")+'"></div>'
   + (it.source_hint? '<div class="mute" style="font-size:11px">'+it.source_hint+'</div>':'');
  cards.appendChild(div);
  div.querySelector("#vis"+k).onchange = e => { state.visible[k]=e.target.checked; draw(); };
  const upd = () => { it.interpreted_label = div.querySelector(".interp").value; it.status = div.querySelector(".status").value; it.note = div.querySelector(".nt").value; it.changed_utc = now(); it.source_hint = null; save(); };
  div.querySelector(".interp").onchange = upd; div.querySelector(".status").onchange = upd; div.querySelector(".nt").oninput = upd; }); }
function save(){ try{ localStorage.setItem("topcon_viewer_"+M.source.sha256.slice(0,12), JSON.stringify({interp:state.interp, annotator:document.getElementById("annotator").value})); }catch(e){} }
function load(){ try{ const s = JSON.parse(localStorage.getItem("topcon_viewer_"+M.source.sha256.slice(0,12))||"null"); if(s){ state.interp = s.interp; document.getElementById("annotator").value = s.annotator||""; } }catch(e){} }
function scheme(label){ if(!label) return null; if(OPTIONS[1][1].some(o=>o[0]===label)) return "TABS-9 (Yang et al. 2010)"; if(OPTIONS[2][1].some(o=>o[0]===label)) return "RETIMAT/OCT-Converter"; return "otro"; }
document.getElementById("export").onclick = () => {
  const out = { schema:"transducin.contour_annotations/1", created_utc: now(), tool:{name:"topcon_contour_viewer", version:M.tool_version},
    source: M.source, geometry: M.geometry,
    native_contours: M.native_labels.map((nl,k)=>({native_label:nl, chunk:"@CONTOUR_INFO", chunk_index:k, header:M.contour_headers[nl], coordinates_modified:false,
      raw_value_range:M.raw_ranges[nl], display_transform:M.display_transform})),
    interpretations: state.interp.map(it=>({native_label:it.native_label, interpreted_label:it.interpreted_label||null, scheme:scheme(it.interpreted_label), status:it.status, method:"manual_visual_review",
      annotator:document.getElementById("annotator").value||null, note:it.note||"", changed_utc:it.changed_utc})),
    edits: [], edits_note:"Reservado: si se corrige una segmentación se conserva aquí el original y la versión editada con el método de edición. Este visor v"+M.tool_version+" no edita coordenadas.",
    clinical_use:"No usar para cálculos clínicos hasta validar la identidad anatómica" };
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{type:"application/json"})); a.download = "topcon_contour_annotations_"+M.source.sha256.slice(0,8)+".json"; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),2000); };
document.getElementById("importBtn").onclick = () => document.getElementById("importFile").click();
document.getElementById("importFile").onchange = e => { const f = e.target.files[0]; if(!f) return; f.text().then(t => { const j = JSON.parse(t);
  if(j.source && j.source.sha256 !== M.source.sha256){ alert("El JSON corresponde a otro archivo (SHA-256 distinto); no se importa."); return; }
  (j.interpretations||[]).forEach(it => { const k = M.native_labels.indexOf(it.native_label); if(k>=0) Object.assign(state.interp[k], {interpreted_label:it.interpreted_label||"", status:it.status, note:it.note||"", changed_utc:it.changed_utc, source_hint:null}); });
  if(j.interpretations && j.interpretations[0] && j.interpretations[0].annotator) document.getElementById("annotator").value = j.interpretations[0].annotator; renderCards(); save(); }); };
document.getElementById("annotator").oninput = save;
document.getElementById("srcinfo").textContent = M.source.device+" · ROM "+M.source.rom+" · "+M.source.software+" · lat. "+M.source.laterality+" · SHA-256 "+M.source.sha256.slice(0,12)+"…";
document.getElementById("evidence").innerHTML = M.evidence.map(x=>"• "+x).join("<br>");
load(); renderCards(); fit(); draw();
</script></body></html>
"""


def _raw_contours(reader: FDAReader, path: Path) -> tuple[dict, dict]:
    raw, headers = {}, {}
    with open(path, "rb") as f:
        for loc, _size in reader._chunk_positions(b"@CONTOUR_INFO"):
            f.seek(loc)
            h = fb.contour_info_header.parse(f.read(34))
            n = h.width * h.height
            arr = np.frombuffer(f.read(n * 2), dtype="<u2").reshape(h.height, h.width)
            raw[h.id] = arr
            headers[h.id] = {
                "method": int(h.method),
                "format": int(h.format),
                "width": int(h.width),
                "height": int(h.height),
            }
    return raw, headers


def build(path: Path, out: Path) -> None:
    reader = FDAReader(path)
    dims = reader._read_oct_dims()
    spacing = reader._read_pixel_spacing(dims)
    raw, headers = _raw_contours(reader, path)
    names = sorted(raw)
    n_b, n_a, height = dims["number_slices"], dims["width"], dims["height"]
    # B-scans: flujos JPEG 2000 de @IMG_JPEG → PNG sin pérdida (los navegadores no leen J2K)
    loc, _ = reader.chunk_dict[b"@IMG_JPEG"]
    images = []
    with open(path, "rb") as f:
        f.seek(loc)
        f.read(25)
        for _ in range(n_b):
            n = struct.unpack("<i", f.read(4))[0]
            im = Image.open(io.BytesIO(f.read(n)))
            if im.mode != "L":
                raise ValueError(f"modo de imagen inesperado {im.mode}: la transcodificación no sería idéntica")
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
            images.append(base64.b64encode(buf.getvalue()).decode("ascii"))
    axial_um, lateral_um = spacing[2] * 1000, spacing[0] * 1000
    # Evidencia resumida (misma transformación que FDAReader)
    ys = {k: height - raw[k][::-1].astype(float) for k in names}
    stats = {k: f"mediana fila {np.median(ys[k]):.0f} ({np.median(ys[k]) * axial_um:.0f} µm)" for k in names}
    evidence = []
    for a, b in itertools.pairwise(names):
        d = (ys[b] - ys[a]) * axial_um
        evidence.append(
            f"{a}→{b}: {np.median(d):.0f} µm (p5 {np.percentile(d, 5):.0f}, p95 {np.percentile(d, 95):.0f})"
        )
    evidence.append(f"{names[0]}→{names[-1]}: mediana {np.median((ys[names[-1]] - ys[names[0]]) * axial_um):.0f} µm")
    evidence.append(
        "Hipótesis H1 (NO adoptada): con TABS (ILM, IS/OS, OS/RPE, BM como las 4 primeras fronteras detectadas) "
        "las separaciones encajarían con RETINA_1=ILM, RETINA_2=IS/OS, RETINA_3=OS/RPE, RETINA_4=BM. "
        "RETIMAT mapea RETINA_2=IZ_RPE y RETINA_3=MZ_EZ, que invierte el orden anatómico."
    )
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()

    # Metadatos no personales: modelo, ROM y versión de software
    def chunk(key: bytes) -> bytes:
        loc2, size = reader._chunk_positions(key)[0]
        with open(path, "rb") as g:
            g.seek(loc2)
            return g.read(size)

    hw = fb.hw_info_03_header.parse(chunk(b"@HW_INFO_03"))
    mm = fb.main_module_info_header.parse(chunk(b"@MAIN_MODULE_INFO"))
    meta = {
        "tool_version": TOOL_VERSION,
        "n_bscans": n_b,
        "n_ascans": n_a,
        "image_height": height,
        "axial_um": axial_um,
        "lateral_um": lateral_um,
        "native_labels": names,
        "stats": stats,
        "evidence": evidence,
        "contour_headers": headers,
        "raw_ranges": {k: [int(raw[k].min()), int(raw[k].max())] for k in names},
        "image_encoding": "JPEG 2000 en el archivo (@IMG_JPEG) → PNG sin pérdida, 8 bits gris",
        "display_transform": {
            "bscan_order": "reversed",
            "y": "H - v",
            "H": height,
            "note": "idéntica a FDAReader.read_layer_volumes(); los valores crudos no se modifican",
        },
        "geometry": {
            "width_px": n_a,
            "bscans": n_b,
            "image_height_px": height,
            "axial_um_per_px": axial_um,
            "lateral_um_per_ascan": lateral_um,
            "pixel_spacing_mm": spacing,
        },
        "source": {
            "sha256": sha,
            "format": "Topcon FDA",
            "device": hw.model_name.strip(),
            "rom": hw.rom_ver.strip(),
            "software": mm.software_name.strip()
            + " "
            + ".".join(str(getattr(mm, f"file_version_{i}")) for i in range(1, 5)),
            "laterality": reader._read_laterality(),
        },
        "default_interpretations": {
            names[0]: {
                "interpreted_label": "ILM",
                "status": "hipotesis_provisional",
                "source_hint": "Hipótesis provisional para visualización (posición compatible); no confirmada.",
            },
            names[-1]: {
                "interpreted_label": "BM/Choroid",
                "status": "hipotesis_provisional",
                "source_hint": "Hipótesis provisional para visualización (posición compatible); no confirmada.",
            },
        },
    }
    html = TEMPLATE.replace(
        "__DATA__",
        json.dumps({"meta": meta, "contours": [raw[k].astype(int).tolist() for k in names], "images": images}).replace(
            "</", "<\\/"
        ),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"OK {out} ({out.stat().st_size / 1e6:.1f} MB) · {n_b} B-scans · contornos {names} · SHA-256 {sha[:12]}…")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fda", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=None)
    a = ap.parse_args()
    build(a.fda, a.output or a.fda.with_suffix(".contour_viewer.html"))


if __name__ == "__main__":
    main()
