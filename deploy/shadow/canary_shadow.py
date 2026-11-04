"""Canario del modo sombra en el entorno real (intérprete y dependencias del nodo). Solo datos sintéticos."""

import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

import transducin
from transducin import shadow_log as sl
from transducin.clinical_data import ETDRSGrid, OCTClinicalData
from transducin.sr_builder import build_sr

assert str(transducin.__file__).startswith(sys.argv[1]), f"importa otro árbol: {transducin.__file__}"
FAKE_NOEL, FAKE_NAME = "ZZZZ19000101", "APELLIDOPRUEBA_NOMBREPRUEBA"
tmp = Path(tempfile.mkdtemp(prefix="shadow-canary-"))
key = tmp / "hmac.key"
key.write_text(os.urandom(32).hex())
os.chmod(key, 0o600)
log_dir = tmp / "logs"


def data():
    g = ETDRSGrid(C=250.0, S1=300.0, N1=310.0, I1=300.0, T1=295.0, S2=270.0, N2=280.0, I2=270.0, T2=265.0)
    return OCTClinicalData(
        noel_id=FAKE_NOEL, vendor="optopol_revo", source_file=f"/no/existe/{FAKE_NAME}_OD.opt",
        study_date="20260101", study_time="101010", laterality="R", study_type="macular",
        patient_name=FAKE_NAME, cmt_um=250.0, etdrs_grid=g, extraction_confidence="confirmed",
    )


def flat(js, p=""):
    if isinstance(js, dict):
        for k, v in js.items():
            yield from flat(v, f"{p}/{k}")
    elif isinstance(js, list):
        for i, v in enumerate(js):
            yield from flat(v, f"{p}[{i}]")
    else:
        yield p, js


def run():
    return dict(flat(build_sr(data(), study_instance_uid="1.2.826.0.1.3680043.8.498.1").to_json_dict()))


for k in ("TRANSDUCIN_SHADOW_LOG_DIR", "TRANSDUCIN_SHADOW_HMAC_KEY_FILE", "TRANSDUCIN_SHADOW_DISABLE"):
    os.environ.pop(k, None)
off1, off2 = run(), run()
noise = {k for k in off1 if off1[k] != off2.get(k)}
os.environ["TRANSDUCIN_SHADOW_LOG_DIR"] = str(log_dir)
os.environ["TRANSDUCIN_SHADOW_HMAC_KEY_FILE"] = str(key)
on = run()
same = {k: v for k, v in on.items() if k not in noise} == {k: v for k, v in off1.items() if k not in noise}
print(f"SR idéntico con log activo: {same} ({len(off1)} nodos, {len(noise)} generados por ejecución)")

files = list(log_dir.glob("shadow-*.jsonl"))
text = "".join(f.read_text() for f in files)
mode = stat.S_IMODE(files[0].stat().st_mode)
dmode = stat.S_IMODE(log_dir.stat().st_mode)
leak = [s for s in (FAKE_NOEL, FAKE_NAME, "20260101", "101010", "250.0", "1.2.826.0.1.3680043.8.498.1") if s in text]
print(f"registros: {len(text.splitlines())} · permisos archivo {oct(mode)} dir {oct(dmode)} · fugas: {leak or 'ninguna'}")

(log_dir / "DISABLE").write_text("")
before = len(text.splitlines())
run()
after = sum(len(f.read_text().splitlines()) for f in log_dir.glob("shadow-*.jsonl"))
print(f"interruptor DISABLE: registros {before} → {after} (sin reiniciar)")
(log_dir / "DISABLE").unlink()

n = 200
d = data()
t0 = time.perf_counter()
for _ in range(n):
    sl.record_emission(d, "webhook", "1.2.3")
dt = (time.perf_counter() - t0) / n * 1000
t0 = time.perf_counter()
for _ in range(20):
    build_sr(data(), study_instance_uid="1.2.3")
sr_ms = (time.perf_counter() - t0) / 20 * 1000
print(f"coste medio de record_emission: {dt:.2f} ms · build_sr completo con log activo: {sr_ms:.1f} ms")
rec = json.loads(text.splitlines()[0])
print("campos:", sorted(rec)[:6], "…", "schema", rec["schema"], "R1", rec["flags"]["R1_laterality"])
ok = same and not leak and mode == 0o600 and before == after
print("CANARIO", "OK" if ok else "FALLÓ")
sys.exit(0 if ok else 1)
