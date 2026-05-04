"""
tests/test_parser.py — Suite pytest para Transducin (JOSS).

Todos los tests usan datos sintéticos (sin PHI).
Fixture: tests/fixtures/synthetic_macular.opt
         Generado por: tests/generate_synthetic_opt.py
"""
import struct
import tempfile
import zlib
from pathlib import Path

import numpy as np
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_macular.opt"
MAGIC   = b"\xa5\xa5\xa5\xff"

# ── helpers para construir .opt mínimos en memoria ────────────────────────────

def _wrap(payload: bytes) -> bytes:
    c = zlib.compress(payload, level=6)
    return b"\x01" + struct.pack("<I", len(c)) + c


def _chunk(name: str, payload: bytes) -> bytes:
    nb = name.encode("ascii")
    return (
        MAGIC
        + struct.pack("<I", len(nb)) + nb + b"\x00"
        + struct.pack("<I", 0) + struct.pack("<I", 0)
        + payload
    )


def _layer_chunk(name: str, arr: np.ndarray) -> bytes:
    n_frames, width = arr.shape
    header = struct.pack("<II", width, n_frames)
    return _chunk(name, _wrap(header + arr.astype("<f4").tobytes()))


# ─────────────────────────────────────────────────────────────────────────────
# test_magic_bytes
# ─────────────────────────────────────────────────────────────────────────────

def test_magic_bytes_invalid():
    """Archivo con magic incorrecto → ValueError, no excepción inesperada."""
    from transducin.revo_opt_reader import read_opt

    bad_data = b"\x00\x00\x00\x00" + b"relleno" * 16
    with tempfile.NamedTemporaryFile(suffix=".opt", delete=False) as f:
        f.write(bad_data)
        path = Path(f.name)

    try:
        with pytest.raises(ValueError, match="magic"):
            read_opt(path)
    finally:
        path.unlink(missing_ok=True)


def test_magic_bytes_file_not_found():
    """Archivo inexistente → FileNotFoundError."""
    from transducin.revo_opt_reader import read_opt

    with pytest.raises(FileNotFoundError):
        read_opt(Path("/tmp/inexistente_transducin_test.opt"))


# ─────────────────────────────────────────────────────────────────────────────
# test_synthetic_macular
# ─────────────────────────────────────────────────────────────────────────────

def test_synthetic_macular_parse_success():
    """El fixture sintético parsea sin excepción."""
    from transducin.revo_opt_reader import read_opt

    result = read_opt(FIXTURE)
    assert result is not None
    assert result["n_frames"] == 128


def test_synthetic_macular_cmt_range():
    """CMT del fixture sintético está en el rango fisiológico esperado (280-320 µm)."""
    from transducin.revo_opt_reader import read_opt

    result = read_opt(FIXTURE)
    cmt = result.get("cmt_um")
    assert cmt is not None, "CMT no extraído"
    assert 280.0 <= cmt <= 320.0, f"CMT fuera de rango: {cmt:.1f} µm"


def test_synthetic_macular_etdrs_nine_sectors():
    """ETDRSGrid tiene los 9 sectores no-None."""
    from transducin.revo_opt_reader import read_opt

    result = read_opt(FIXTURE)
    etdrs = result.get("etdrs")
    assert etdrs is not None, "ETDRS no extraído"

    sectors = [etdrs.C, etdrs.S1, etdrs.N1, etdrs.I1, etdrs.T1,
               etdrs.S2, etdrs.N2, etdrs.I2, etdrs.T2]
    nones = [i for i, v in enumerate(sectors) if v is None]
    assert not nones, f"Sectores None en posiciones {nones}"


def test_synthetic_macular_myopi():
    """Biometría MYOPI extraída correctamente del fixture."""
    from transducin.revo_opt_reader import read_opt

    result = read_opt(FIXTURE)
    myopi = result.get("myopi")
    assert myopi is not None
    assert abs(myopi["al"]  - 23.5) < 0.01
    assert abs(myopi["k1"]  - 43.0) < 0.01
    assert abs(myopi["k2"]  - 44.0) < 0.01
    assert myopi["cct"] == 545


# ─────────────────────────────────────────────────────────────────────────────
# test_bm_bottom_fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_bm_bottom_fallback():
    """
    SOCT 11.5.0 guarda BM como 'BOTTOM'; 11.5.3 lo guarda como 'BM'.
    _extract_layer_with_fallback debe obtener CMT válido con chunk BOTTOM.
    """
    from transducin.revo_opt_reader import read_opt

    N_B, N_A = 64, 256
    top = np.full((N_B, N_A), 640.0, dtype=np.float32)
    bm  = np.full((N_B, N_A), 751.0, dtype=np.float32)  # 111px × 2.8 = 310.8 µm

    lat = 10.0 / N_A
    octparams_recs = b"".join([
        bytes([3, 0, 0, 0x22]) + struct.pack("<f", 10.0),    # scan_width_mm
        bytes([5, 0, 0, 0x12]) + struct.pack("<I", N_A),     # n_ascans
        bytes([6, 0, 0, 0x12]) + struct.pack("<I", N_B),     # n_bscans
        bytes([8, 0, 0, 0x22]) + struct.pack("<f", lat),     # lateral_mm
        bytes([9, 0, 0, 0x22]) + struct.pack("<f", 0.0028),  # axial_mm
        bytes([11, 0, 0, 0x12]) + struct.pack("<I", 992),    # depth_px
    ])
    params_text = b"SYNTH 20260101 1.2.826.0.1.3680043.8.498.99999999999 SYNTH00000000\x00"

    data = b"".join([
        _chunk("PARAMS",    params_text),
        _chunk("OCTPARAMS", _wrap(octparams_recs)),
        _layer_chunk("TOP",    top),
        _layer_chunk("BOTTOM", bm),   # ← BOTTOM en lugar de BM
    ])

    with tempfile.NamedTemporaryFile(suffix=".opt", delete=False) as f:
        f.write(data)
        path = Path(f.name)

    try:
        result = read_opt(path)
        cmt = result.get("cmt_um")
        assert cmt is not None, "CMT no extraído con chunk BOTTOM"
        assert 280.0 <= cmt <= 320.0, f"CMT fuera de rango con BOTTOM: {cmt:.1f} µm"
    finally:
        path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# test_noel_id_format
# ─────────────────────────────────────────────────────────────────────────────

def test_noel_id_valid():
    """JAHJ19870831 es un PatientID NOEL válido."""
    from transducin.noel_id import is_valid_noel

    assert is_valid_noel("JAHJ19870831") is True


def test_noel_id_invalid_cases():
    """IDs con formato incorrecto son inválidos."""
    from transducin.noel_id import is_valid_noel

    invalid = [
        "jahj19870831",    # minúsculas
        "JAHJ198708",      # solo 6 dígitos
        "JAHJK19870831",   # 5 letras
        "12JAHJ870831",    # dígitos al inicio
        "",                # vacío
        "JAHJ 19870831",   # espacio interno
    ]
    for pid in invalid:
        assert is_valid_noel(pid) is False, f"Debería ser inválido: {pid!r}"


def test_noel_id_build_canonical():
    """build_noel_id produce el ID canónico del protocolo NOEL."""
    from transducin.noel_id import build_noel_id

    assert build_noel_id("JAURRIETA HINOJOS^JESUS NOEL", "19870831") == "JAHJ19870831"


# ─────────────────────────────────────────────────────────────────────────────
# test_etdrs_none_when_empty
# ─────────────────────────────────────────────────────────────────────────────

def test_etdrs_none_when_bm_zeros():
    """
    Cuando BM = 0 (chunk ausente o corrupto), el grosor es negativo → inválido.
    compute_etdrs debe retornar None (ningún sector con datos suficientes).
    """
    from transducin.revo_opt_reader import compute_etdrs

    N_B, N_A = 64, 256
    top = np.full((N_B, N_A), 640.0, dtype=np.float32)
    bm  = np.zeros((N_B, N_A), dtype=np.float32)   # BM=0 → grosor = −640px (inválido)

    params = {
        "axial_um":      2.800,
        "lateral_um":    39.063,   # 10000/256
        "scan_width_mm": 10.0,
        "n_bscans":      N_B,
        "n_ascans":      N_A,
        "depth_px":      992,
    }

    etdrs = compute_etdrs(top, bm, params)
    assert etdrs is None, f"Se esperaba None con BM=0, pero se obtuvo: {etdrs}"


# ─────────────────────────────────────────────────────────────────────────────
# test_filename_parsing — Querétaro v11.5.x format
# ─────────────────────────────────────────────────────────────────────────────

def test_filename_queretaro_od():
    """Site-B OD filename (spaces in name, 'R' laterality) parses correctly."""
    from transducin.opt_extractor import _parse_revo_filename, _map_study_type

    fname = "SYNTHETIC PATIENT_SITE B_20260209_171752_R_OCT.opt"
    g = _parse_revo_filename(fname)
    assert g is not None, "Filename Site-B OD no parseado"
    assert g["lat"] == "R", f"Laterality esperada 'R', obtenida '{g['lat']}'"
    assert _map_study_type(g["type"]) == "macular", f"study_type esperado 'macular', obtenido '{_map_study_type(g['type'])}'"


def test_filename_queretaro_os():
    """Site-B OS filename ('L' laterality) parses correctly."""
    from transducin.opt_extractor import _parse_revo_filename

    fname = "GARCIA_LOPEZ_MARIA_20260209_143000_L_OCT.opt"
    g = _parse_revo_filename(fname)
    assert g is not None, "Filename Site-B OS no parseado"
    assert g["lat"] == "L", f"Laterality esperada 'L', obtenida '{g['lat']}'"


def test_octparams_tag23_laterality():
    """parse_octparams extrae tag 23 y scan_center_x_mm tiene el signo correcto."""
    import struct
    import zlib
    from transducin.revo_opt_reader import parse_octparams

    def _make_octparams_block(tag23_val: float) -> bytes:
        # Build a minimal OCTPARAMS decompressed block with only tag 23.
        # Format: tag(1B) | pad(2B) | type(1B=0x22 float32) | val(4B LE)
        record = bytes([23, 0, 0, 0x22]) + struct.pack("<f", tag23_val)
        compressed = zlib.compress(record)
        # _decompress_block expects: 1B prefix + 4B LE compressed_size + data
        return bytes([0x01]) + struct.pack("<I", len(compressed)) + compressed

    def _fake_chunks(payload: bytes) -> dict:
        return {"OCTPARAMS": {"offset": 0, "real_size": len(payload)}}

    # OS (left eye) — positive tag 23
    payload_os = _make_octparams_block(+3.6)
    params_os  = parse_octparams(payload_os, _fake_chunks(payload_os))
    assert params_os["scan_center_x_mm"] is not None
    assert params_os["scan_center_x_mm"] > 0, "OS debe ser positivo"

    # OD (right eye) — negative tag 23
    payload_od = _make_octparams_block(-3.6)
    params_od  = parse_octparams(payload_od, _fake_chunks(payload_od))
    assert params_od["scan_center_x_mm"] is not None
    assert params_od["scan_center_x_mm"] < 0, "OD debe ser negativo"

    # Absent OCTPARAMS → scan_center_x_mm = None (no crash)
    params_empty = parse_octparams(b"", {})
    assert params_empty["scan_center_x_mm"] is None


def test_series_description_od():
    """build_dicom_oct construye SeriesDescription correcta para OD."""
    lat_str = "OD" if "R" == "R" else "OS"
    type_lbl = "macular".replace("_", " ").title()
    desc = f"Revo FC130 {type_lbl} {lat_str}"
    assert desc == "Revo FC130 Macular OD"


# ─────────────────────────────────────────────────────────────────────────────
# test_study_type_from_chunks
# ─────────────────────────────────────────────────────────────────────────────

def _make_study_type_opt(chunk_names: list, n_bscans: int = 0) -> bytes:
    """Minimal .opt with PARAMS + optional OCTPARAMS(n_bscans) + named chunks."""
    params_text = b"SYNTH 20260101 1.2.826.0.1.3680043.8.498.99999999999 SYNTH00000000\x00"
    data = _chunk("PARAMS", params_text)
    if n_bscans:
        rec = bytes([6, 0, 0, 0x12]) + struct.pack("<I", n_bscans)
        data += _chunk("OCTPARAMS", _wrap(rec))
    for name in chunk_names:
        data += _chunk(name, _wrap(struct.pack("<II", 1, 1) + b"\x00\x00\x00\x00"))
    return data


def test_study_type_inferred_angio():
    """ANGPRV chunk presente → study_type='angio'."""
    from transducin.opt_extractor import extract_from_opt

    with tempfile.NamedTemporaryFile(suffix=".opt", delete=False) as f:
        f.write(_make_study_type_opt(["ANGPRV"]))
        path = Path(f.name)
    try:
        cd = extract_from_opt(path)
        assert cd.study_type == "angio", f"Esperado 'angio', obtenido '{cd.study_type}'"
    finally:
        path.unlink(missing_ok=True)


def test_study_type_inferred_widefield():
    """FNDSRECO sin EYE → study_type='widefield'."""
    from transducin.opt_extractor import extract_from_opt

    with tempfile.NamedTemporaryFile(suffix=".opt", delete=False) as f:
        f.write(_make_study_type_opt(["FNDSRECO"]))
        path = Path(f.name)
    try:
        cd = extract_from_opt(path)
        assert cd.study_type == "widefield", f"Esperado 'widefield', obtenido '{cd.study_type}'"
    finally:
        path.unlink(missing_ok=True)


def test_study_type_inferred_macular():
    """EYE + FNDSRECO + n_bscans=128 → study_type='macular' (EYE tiene prioridad)."""
    from transducin.opt_extractor import extract_from_opt

    with tempfile.NamedTemporaryFile(suffix=".opt", delete=False) as f:
        f.write(_make_study_type_opt(["FNDSRECO", "EYE"], n_bscans=128))
        path = Path(f.name)
    try:
        cd = extract_from_opt(path)
        assert cd.study_type == "macular", f"Esperado 'macular', obtenido '{cd.study_type}'"
    finally:
        path.unlink(missing_ok=True)


def test_study_type_inferred_optic_nerve_dmarkers():
    """DMARKERS presente → study_type='optic_nerve' (validado REVO60+REVO130, ≥100 frames)."""
    from transducin.opt_extractor import extract_from_opt

    with tempfile.NamedTemporaryFile(suffix=".opt", delete=False) as f:
        f.write(_make_study_type_opt(["EYE", "DMARKERS"], n_bscans=192))
        path = Path(f.name)
    try:
        cd = extract_from_opt(path)
        assert cd.study_type == "optic_nerve", f"Esperado 'optic_nerve', obtenido '{cd.study_type}'"
    finally:
        path.unlink(missing_ok=True)


def test_study_type_inferred_optic_nerve_fallback():
    """EYE + n_bscans=18 sin DMARKERS → 'optic_nerve' por fallback de n_frames."""
    from transducin.opt_extractor import extract_from_opt

    with tempfile.NamedTemporaryFile(suffix=".opt", delete=False) as f:
        f.write(_make_study_type_opt(["EYE"], n_bscans=18))
        path = Path(f.name)
    try:
        cd = extract_from_opt(path)
        assert cd.study_type == "optic_nerve", f"Esperado 'optic_nerve', obtenido '{cd.study_type}'"
    finally:
        path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# test_study_type_keyword_fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_study_type_keyword_biometria():
    """'biometria.opt' → study_type='biometry' por keyword 'biometr'."""
    from transducin.opt_extractor import extract_from_opt

    data = _make_study_type_opt([])   # sin chunks → chunks no pueden inferir
    with tempfile.NamedTemporaryFile(suffix=".opt", prefix="biometria_", delete=False) as f:
        f.write(data)
        path = Path(f.name)
    try:
        cd = extract_from_opt(path)
        assert cd.study_type == "biometry", f"Esperado 'biometry', obtenido '{cd.study_type}'"
    finally:
        path.unlink(missing_ok=True)


def test_study_type_keyword_calculo_lio():
    """'calculo_lio.opt' → study_type='biometry' por keyword 'calculo'."""
    from transducin.opt_extractor import extract_from_opt

    data = _make_study_type_opt([])
    with tempfile.NamedTemporaryFile(suffix=".opt", prefix="calculo_lio_", delete=False) as f:
        f.write(data)
        path = Path(f.name)
    try:
        cd = extract_from_opt(path)
        assert cd.study_type == "biometry", f"Esperado 'biometry', obtenido '{cd.study_type}'"
    finally:
        path.unlink(missing_ok=True)
