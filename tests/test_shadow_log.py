"""Pruebas del registro en modo sombra (``transducin.shadow_log``). Todas sintéticas."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

import transducin.shadow_log as sl
from transducin.clinical_data import ETDRSGrid, OCTClinicalData

KEY = b"0123456789abcdef0123456789abcdef"
FAKE_NOEL = "ZZZZ19000101"  # identificador sintético, no corresponde a nadie
FAKE_NAME = "APELLIDOPRUEBA_NOMBREPRUEBA"


@pytest.fixture(autouse=True)
def _reset_purge():
    sl._last_purge_day = None
    yield
    sl._last_purge_day = None


def _cfg(tmp_path: Path, days: int = 30) -> sl.ShadowConfig:
    return sl.ShadowConfig(log_dir=tmp_path / "shadow", key=KEY, retention_days=days)


def _grid() -> ETDRSGrid:
    return ETDRSGrid(C=250.0, S1=300.0, N1=310.0, I1=300.0, T1=295.0, S2=270.0, N2=280.0, I2=270.0, T2=265.0)


def _data(vendor="optopol_revo", src="estudio.opt", lat="R", cmt=250.0, etdrs=True, notes=None) -> OCTClinicalData:
    d = OCTClinicalData(
        noel_id=FAKE_NOEL,
        vendor=vendor,
        source_file=src,
        study_date="20260101",
        study_time="101010",
        laterality=lat,
        study_type="macular",
        patient_name=FAKE_NAME,
        patient_dob="19000101",
        cmt_um=cmt,
        etdrs_grid=_grid() if etdrs else None,
        extraction_confidence="confirmed",
    )
    for n in notes or []:
        d.add_note(n)
    return d


# ── Configuración y seguridad ─────────────────────────────────────────────────


def test_disabled_by_default_and_writes_nothing(tmp_path):
    assert sl.load_config({}) is None
    assert sl.record_emission(_data(), "sr", "1.2.3", config=None) is None  # sin entorno → no-op
    assert not list(tmp_path.iterdir())


def test_requires_key_and_outside_git(tmp_path):
    assert sl.load_config({"TRANSDUCIN_SHADOW_LOG_DIR": str(tmp_path / "l")}) is None  # sin clave
    assert (
        sl.load_config({"TRANSDUCIN_SHADOW_LOG_DIR": str(tmp_path / "l"), "TRANSDUCIN_SHADOW_HMAC_KEY": "corta"})
        is None
    )
    ok = sl.load_config({"TRANSDUCIN_SHADOW_LOG_DIR": str(tmp_path / "l"), "TRANSDUCIN_SHADOW_HMAC_KEY": KEY.decode()})
    assert ok is not None and ok.retention_days == 30
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    env = {"TRANSDUCIN_SHADOW_LOG_DIR": str(repo / "logs"), "TRANSDUCIN_SHADOW_HMAC_KEY": KEY.decode()}
    assert sl.load_config(env) is None  # dentro de un árbol Git


def test_key_file_must_be_private(tmp_path):
    kf = tmp_path / "k"
    kf.write_bytes(KEY)
    os.chmod(kf, 0o644)
    env = {"TRANSDUCIN_SHADOW_LOG_DIR": str(tmp_path / "l"), "TRANSDUCIN_SHADOW_HMAC_KEY_FILE": str(kf)}
    assert sl.load_config(env) is None
    os.chmod(kf, 0o600)
    assert sl.load_config(env) is not None


def test_retention_env(tmp_path):
    env = {
        "TRANSDUCIN_SHADOW_LOG_DIR": str(tmp_path / "l"),
        "TRANSDUCIN_SHADOW_HMAC_KEY": KEY.decode(),
        "TRANSDUCIN_SHADOW_RETENTION_DAYS": "7",
    }
    assert sl.load_config(env).retention_days == 7
    env["TRANSDUCIN_SHADOW_RETENTION_DAYS"] = "x"
    assert sl.load_config(env).retention_days == 30


# ── Sin PHI ───────────────────────────────────────────────────────────────────


def test_log_contains_no_phi_or_measurements(tmp_path):
    cfg = _cfg(tmp_path)
    d = _data(src=f"{FAKE_NAME}_OD_20260101.opt", notes=["CONFIRMED: laterality, ... — desde filename"])
    sl.record_emission(d, "sr", "9.8.7.6.5", config=cfg)
    sl.record_emission(d, "webhook", "9.8.7.6.5", config=cfg)
    text = "".join(p.read_text() for p in cfg.log_dir.glob("shadow-*.jsonl"))
    for secret in (FAKE_NOEL, FAKE_NAME, "19000101", "20260101", "101010", "9.8.7.6.5", "250.0", "estudio", ".opt"):
        assert secret not in text, secret
    rec = json.loads(text.splitlines()[0])
    assert rec["patient_hmac"] and rec["study_hmac"] and len(rec["patient_hmac"]) == 32


def test_hmac_is_stable_keyed_and_empty_safe():
    assert sl.pseudonym(KEY, "x") == sl.pseudonym(KEY, "x")
    assert sl.pseudonym(KEY, "x") != sl.pseudonym(KEY + b"!", "x")
    assert sl.pseudonym(KEY, "") == ""


def test_file_permissions_private(tmp_path):
    cfg = _cfg(tmp_path)
    sl.record_emission(_data(), "sr", "1", config=cfg)
    f = next(cfg.log_dir.glob("shadow-*.jsonl"))
    assert (f.stat().st_mode & 0o777) == 0o600


# ── Reglas R1–R6 por ruta ─────────────────────────────────────────────────────


def test_revo_risks():
    a = sl.assess(_data("optopol_revo", "a.opt", lat=""), "sr")
    f = a["flags"]
    assert a["route"] == "revo_opt" and a["value_origin"] == "reconstructed_by_transducin"
    assert f["R1_laterality"] == "defaulted"
    assert f["R2_overlap_sectors"] is True and f["R3_cirrus_nasal_wedge"] is False
    assert f["R4_orientation_unvalidated"] is True
    assert f["R5_geometric_center_as_foveal"] == "yes" and a["center_origin"] == "geometric_volume_center"
    assert f["R6_no_provenance_or_validation"] is True


def test_revo_laterality_sources():
    fn = sl.assess(_data(notes=["CONFIRMED: laterality, study_date — desde filename"]), "sr")
    tag = sl.assess(_data(notes=["ASSUMED: laterality=L — OCTPARAMS tag 23 scan_center_x_mm=1.0"]), "sr")
    assert fn["laterality_source"] == "filename" and tag["laterality_source"] == "octparams_tag23"
    # sin archivo accesible no hay evidencia que cotejar: se declara, no se supone
    assert fn["flags"]["R1_laterality"] == "no_evidence_available" and fn["laterality_evidence"]["state"] == "unknown"


def test_cirrus_dcm_vs_pdf():
    dcm = sl.assess(_data("zeiss_cirrus", "x.dcm"), "webhook")
    pdf = sl.assess(_data("zeiss_cirrus", "x.PDF"), "webhook")
    assert dcm["route"] == "cirrus_dcm" and dcm["flags"]["R3_cirrus_nasal_wedge"] is True
    assert dcm["flags"]["R2_overlap_sectors"] is False
    assert dcm["flags"]["R5_geometric_center_as_foveal"] == "possible"
    assert pdf["route"] == "cirrus_pdf" and pdf["value_origin"] == "vendor_original_ocr"
    assert pdf["flags"]["R3_cirrus_nasal_wedge"] is False and pdf["flags"]["R5_geometric_center_as_foveal"] == "no"


@pytest.mark.parametrize(
    "vendor,route",
    [("heidelberg_spectralis", "spectralis_e2e"), ("topcon_dri_oct", "topcon_fda"), ("topcon_triton", "topcon_fda")],
)
def test_d1_d2_routes(vendor, route):
    a = sl.assess(_data(vendor, "x.fda"), "sr")
    assert (
        a["route"] == route
        and a["flags"]["R2_overlap_sectors"]
        and a["flags"]["R5_geometric_center_as_foveal"] == "yes"
    )
    assert a["laterality_source"] == "native_metadata"


def test_no_measurements_means_no_r5_r6_r2():
    a = sl.assess(_data(cmt=None, etdrs=False), "sr")
    f = a["flags"]
    assert f["R5_geometric_center_as_foveal"] == "not_applicable"
    assert f["R6_no_provenance_or_validation"] is False and f["R2_overlap_sectors"] is False


def test_other_laterality_value_and_unknown_vendor():
    a = sl.assess(_data("acme", "x.bin", lat="OU"), "sr")
    assert a["route"] == "other:acme" and a["flags"]["R1_laterality"] == "other_value"


# ── Escritura, retención y robustez ───────────────────────────────────────────


def test_purge_removes_only_old_files(tmp_path):
    cfg = _cfg(tmp_path, days=30)
    cfg.log_dir.mkdir(parents=True)
    old = cfg.log_dir / "shadow-20250101.jsonl"
    old.write_text("{}\n")
    keep = cfg.log_dir / "otro.txt"
    keep.write_text("no tocar")
    t_old = time.time() - 40 * 86400
    os.utime(old, (t_old, t_old))
    sl.record_emission(_data(), "sr", "1", config=cfg)
    assert not old.exists() and keep.exists()


def test_recent_files_survive_purge(tmp_path):
    cfg = _cfg(tmp_path, days=30)
    cfg.log_dir.mkdir(parents=True)
    recent = cfg.log_dir / "shadow-20260101.jsonl"
    recent.write_text("{}\n")
    t = time.time() - 5 * 86400
    os.utime(recent, (t, t))
    sl.record_emission(_data(), "sr", "1", config=cfg)
    assert recent.exists()


def test_never_raises_on_failure(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    bad = sl.ShadowConfig(log_dir=blocker / "sub", key=KEY)  # un archivo como directorio padre
    assert sl.record_emission(_data(), "sr", "1", config=bad) is None
    assert sl.record_emission(object(), "sr", "1", config=_cfg(tmp_path)) is None  # objeto inválido


def test_aggregate_counts(tmp_path):
    cfg = _cfg(tmp_path)
    for lat in ("R", "", "L"):
        sl.record_emission(_data(lat=lat), "sr", f"u{lat}", config=cfg)
    sl.record_emission(_data("zeiss_cirrus", "a.dcm"), "webhook", "c", config=cfg)
    agg = sl.aggregate(sl.read_records(cfg.log_dir))
    assert agg["n_records"] == 4
    revo = agg["by_route"]["revo_opt|sr"]
    assert revo["n"] == 3 and revo["flags"]["R1_laterality"] == {"no_evidence_available": 2, "defaulted": 1}
    assert agg["by_route"]["cirrus_dcm|webhook"]["flags"]["R3_cirrus_nasal_wedge"] == {"True": 1}


# ── No altera ninguna emisión ─────────────────────────────────────────────────


@contextmanager
def _shadow_env(tmp_path, on: bool):
    keys = ("TRANSDUCIN_SHADOW_LOG_DIR", "TRANSDUCIN_SHADOW_HMAC_KEY")
    old = {k: os.environ.get(k) for k in keys}
    try:
        if on:
            os.environ["TRANSDUCIN_SHADOW_LOG_DIR"] = str(tmp_path / "live")
            os.environ["TRANSDUCIN_SHADOW_HMAC_KEY"] = KEY.decode()
        else:
            for k in keys:
                os.environ.pop(k, None)
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _flatten(js, path=""):
    if isinstance(js, dict):
        for k, v in js.items():
            yield from _flatten(v, f"{path}/{k}")
    elif isinstance(js, list):
        for i, v in enumerate(js):
            yield from _flatten(v, f"{path}[{i}]")
    else:
        yield path, js


def test_build_sr_output_is_identical_with_shadow_on_and_off(tmp_path):
    from transducin.sr_builder import build_sr

    def run():
        return dict(_flatten(build_sr(_data(), study_instance_uid="1.2.826.0.1.3680043.8.498.1").to_json_dict()))

    with _shadow_env(tmp_path, False):
        off1, off2 = run(), run()
    noise = {k for k in off1 if off1[k] != off2.get(k)}  # UIDs/fecha-hora generados en cada ejecución
    with _shadow_env(tmp_path, True):
        on = run()
    assert {k: v for k, v in on.items() if k not in noise} == {k: v for k, v in off1.items() if k not in noise}
    assert list((tmp_path / "live").glob("shadow-*.jsonl")), "debió registrar la emisión SR"


def test_webhook_payload_is_identical_with_shadow_on_and_off(tmp_path, monkeypatch):
    import transducin.hot_folder_watcher as w

    sent: list[bytes] = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        sent.append(req.data)
        return _Resp()

    monkeypatch.setattr(w, "GRAEFE_WEBHOOK_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(w, "GRAEFE_INSTRUMENT_KEY", "k")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with _shadow_env(tmp_path, False):
        w._notify_graefe(_data(), "1.2.3")
    with _shadow_env(tmp_path, True):
        w._notify_graefe(_data(), "1.2.3")
    assert len(sent) == 2 and sent[0] == sent[1]
    recs = sl.read_records(tmp_path / "live")
    assert len(recs) == 1 and recs[0]["emission"] == "webhook"


# ── Interruptor de desactivación inmediata ────────────────────────────────────


def test_kill_switch_file_and_env(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    assert sl.record_emission(_data(), "sr", "1", config=cfg) is not None
    (cfg.log_dir / "DISABLE").write_text("")
    assert sl.record_emission(_data(), "sr", "2", config=cfg) is None  # sin reiniciar nada
    (cfg.log_dir / "DISABLE").unlink()
    assert sl.record_emission(_data(), "sr", "3", config=cfg) is not None
    monkeypatch.setenv("TRANSDUCIN_SHADOW_DISABLE", "1")
    assert sl.record_emission(_data(), "sr", "4", config=cfg) is None
    n = sum(len(p.read_text().splitlines()) for p in cfg.log_dir.glob("shadow-*.jsonl"))
    assert n == 2


def test_record_includes_evidence_without_filename_or_phi(tmp_path):
    cfg = _cfg(tmp_path)
    p = tmp_path / f"{FAKE_NAME}_OD_x.dcm"
    p.write_bytes(b"no es dicom")  # ilegible: se registra como no disponible, sin fallar
    d = _data("zeiss_cirrus", str(p), lat="R")
    rec = sl.record_emission(d, "sr", "1.2.3", config=cfg)
    ev = rec["laterality_evidence"]
    assert "dicom_unreadable" in ev["unavailable"] or ev["sources"]
    text = "".join(f.read_text() for f in cfg.log_dir.glob("shadow-*.jsonl"))
    assert FAKE_NAME not in text and str(tmp_path) not in text
    assert rec["flags"]["R1_laterality"] in {"inferred_only", "no_evidence_available"}


def test_aggregate_exposes_evidence_and_unique_studies(tmp_path):
    cfg = _cfg(tmp_path)
    sl.record_emission(_data(), "sr", "same", config=cfg)
    sl.record_emission(_data(), "webhook", "same", config=cfg)  # misma emisión de estudio por otro destino
    agg = sl.aggregate(sl.read_records(cfg.log_dir))
    assert agg["n_records"] == 2 and agg["unique_studies"]["revo_opt"] == 1
    assert "revo_opt|macular" in agg["by_route_study_type"]
    assert agg["by_route"]["revo_opt|sr"]["laterality_state"]
