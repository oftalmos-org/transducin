"""Constructores de archivos .e2e y .fda SINTÉTICOS (sin datos de pacientes).

Solo escriben la estructura mínima que leen E2EReader / FDAReader: directorio y
chunks de contornos, metadata de B-scan y lateralidad (E2E); chunks de
captura, escaneo y @CONTOUR_INFO (FDA). Los píxeles de imagen no se generan.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from transducin.readers import _e2e_binary as eb
from transducin.readers import _fda_binary as fb

NAN = float("nan")


def thickness_profile(n_b: int, n_a: int, base_um: float, grad_um: float = 0.0) -> np.ndarray:
    """Espesor [n_b × n_a] en µm; grad_um suma de 0 a grad_um a lo largo de las A-scans."""
    return base_um + np.tile(np.linspace(0.0, grad_um, n_a), (n_b, 1))


# ── E2E ───────────────────────────────────────────────────────────────────────


def _e2e_chunk(type_: int, payload: bytes, slice_id: int = -1, series_id: int = 1) -> bytes:
    head = eb.chunk_structure.build(
        {
            "magic3": "CMDb",
            "unknown": 0,
            "unknown2": 0,
            "pos": 0,
            "size": len(payload),
            "unknown3": 0,
            "patient_db_id": 1,
            "study_id": 1,
            "series_id": series_id,
            "slice_id": slice_id,
            "ind": 1,
            "unknown4": 0,
            "type": type_,
            "unknown5": 0,
        }
    )
    return head + payload


def build_e2e(
    path: Path,
    *,
    laterality: str | None = "R",
    axial_mm: float = 0.00387,
    scan_mm: float = 6.0,
    n_b: int = 25,
    n_a: int = 256,
    ilm_px: float = 100.0,
    thick_um: np.ndarray | None = None,  # [n_b × n_a]; default 300 µm
    nfl_um: float = 40.0,
    gcl_ipl_um: float = 60.0,
    quality: float | None = 0.25,
    with_contours: bool = True,
    with_image: bool = False,
) -> Path:
    """Escribe un .e2e mínimo con un cubo macular sintético."""
    axial_um = axial_mm * 1000.0
    if thick_um is None:
        thick_um = np.full((n_b, n_a), 300.0)

    ilm = np.full((n_b, n_a), ilm_px, dtype=np.float32)
    bm = ilm + thick_um / axial_um
    nfl = ilm + nfl_um / axial_um
    inl = nfl + gcl_ipl_um / axial_um

    chunks: list[tuple[int, int, bytes]] = []  # (type, slice_id, bytes)
    if laterality:
        chunks.append((3, -1, _e2e_chunk(3, eb.pre_data.build({"unknown": 0, "laterality": laterality}))))

    q_bits = struct.unpack("<I", struct.pack("<f", quality if quality is not None else 5.0))[0]
    for i in range(n_b):
        posy = scan_mm * i / max(n_b - 1, 1)
        meta = eb.bscan_metadata.build(
            {
                "unknown1": 0,
                "imgSizeY": 496,
                "imgSizeX": n_a,
                "posX1": 0.0,
                "posY1": posy,
                "posX2": scan_mm,
                "posY2": posy,
                "zero1": 0,
                "unknown2": 0.0,
                "scaley": axial_mm,
                "unknown3": 0.0,
                "zero2": 0,
                "unknown4": [0.0, 0.0],
                "zero3": 0,
                "imgSizeWidth": n_a,
                "numImages": n_b,
                "aktImage": i,
                "scanType": 0,
                "centrePosX": 0.0,
                "centrePosY": 0.0,
                "unknown5": q_bits,  # byte 84: lo lee el extractor como float
                "acquisitionTime": (1_700_000_000 + 11_644_473_600) * 10_000_000,
                "numAve": 9,
                "imgQuality": 0.0,
            }
        )
        chunks.append((10004, 2 * i, _e2e_chunk(10004, meta, slice_id=2 * i)))

    if with_contours:
        for cid, arr in ((0, ilm), (9, bm), (1, nfl), (3, inl)):
            for i in range(n_b):
                payload = (
                    eb.contour_structure.build({"unknown0": 0, "id": cid, "unknown1": 0, "width": n_a})
                    + arr[i].astype("<f4").tobytes()
                )
                chunks.append((10019, 2 * i, _e2e_chunk(10019, payload, slice_id=2 * i)))

    if with_image:  # 1 imagen mínima por slice, para el lector completo de oct_converter
        for i in range(n_b):
            pix = np.full((4, 4), 100, dtype="<u2").tobytes()
            payload = (
                eb.image_structure.build({"size": len(pix), "type": 0, "unknown": 0, "height": 4, "width": 4}) + pix
            )
            chunks.append((1073741824, 2 * i, _e2e_chunk(1073741824, payload, slice_id=2 * i)))

    dir_pos = 88
    body_start = dir_pos + 52 + 44 * len(chunks)
    entries, blobs, off = [], [], body_start
    for type_, slice_id, blob in chunks:
        entries.append(
            eb.sub_directory_structure.build(
                {
                    "pos": 0,
                    "start": off,
                    "size": len(blob),
                    "unknown": 0,
                    "patient_db_id": 1,
                    "study_id": 1,
                    "series_id": 1,
                    "slice_id": slice_id,
                    "unknown2": 0,
                    "unknown3": 0,
                    "type": type_,
                    "unknown4": 0,
                }
            )
        )
        blobs.append(blob)
        off += len(blob)

    def maindir(num, current, prev):
        return eb.main_directory_structure.build(
            {
                "magic2": "CMDb",
                "version": 0,
                "unknown": [0] * 10,
                "num_entries": num,
                "current": current,
                "prev": prev,
                "unknown3": 0,
            }
        )

    out = (
        eb.header_structure.build({"magic1": "CMDb", "version": 0, "unknown": [0] * 10})
        + maindir(0, dir_pos, 0)
        + maindir(len(chunks), 0, 0)
        + b"".join(entries)
        + b"".join(blobs)
    )
    path.write_bytes(out)
    return path


# ── FDA ───────────────────────────────────────────────────────────────────────


def _fda_chunk(name: bytes, data: bytes) -> bytes:
    return bytes([len(name)]) + name + struct.pack("<I", len(data)) + data


def build_fda(
    path: Path,
    *,
    eye: int | None = 0,  # 0 = derecho, 1 = izquierdo
    n_b: int = 25,
    n_a: int = 256,
    img_h: int = 480,
    axial_um: float = 2.6,
    scan_mm: float = 6.0,
    ilm_px: float = 100.0,
    thick_px: np.ndarray | None = None,  # [n_b × n_a] en px; default 100 px
    nfl_px: int = 15,
    gcl_ipl_px: int = 23,
    quality: str | None = "85",
    with_contours: bool = True,
    zero_layers: tuple[str, ...] = (),
    layer_ids: dict[str, str] | None = None,
    model_name: str | None = None,  # si se da, escribe @HW_INFO_03 con ese modelo
    rom_ver: str = "1.10",  # renombra MULTILAYERS_n (p. ej. a RETINA_n)
) -> Path:
    """Escribe un .fda mínimo (sin píxeles de imagen) con segmentación sintética."""
    if thick_px is None:
        thick_px = np.full((n_b, n_a), 100.0)
    ilm = np.full((n_b, n_a), ilm_px)
    layers = {
        "MULTILAYERS_1": ilm,
        "MULTILAYERS_2": ilm + nfl_px,
        "MULTILAYERS_4": ilm + nfl_px + gcl_ipl_px,
        "MULTILAYERS_7": ilm + thick_px,
    }
    out = fb.header.build({"file_code": "FOCT", "file_type": "FDA", "major_ver": 1, "minor_ver": 0})
    if model_name is not None:
        hw = {"model_name": model_name, "serial_number": "SYNTH", "spect_sn": "", "rom_ver": rom_ver, "unknown": ""}
        for k in ("eq_calib", "spect_calib"):
            for u in ("year", "month", "day", "hour", "minute"):
                hw[f"{k}_{u}"] = 0
        out += _fda_chunk(b"@HW_INFO_03", fb.hw_info_03_header.build(hw))
    if eye is not None:
        out += _fda_chunk(
            b"@CAPTURE_INFO_02",
            fb.capture_info_02_header.build(
                {"eye": eye, "scan_mode": 0, "session_id": 0, "label": "", "cap_date": [2025, 3, 14, 10, 30, 0]}
            ),
        )
    out += _fda_chunk(
        b"@IMG_JPEG",
        fb.oct_header.build(
            {
                "scan_mode": 0,
                "unknown1": 0,
                "unknown2": 0,
                "width": n_a,
                "height": img_h,
                "number_slices": n_b,
                "unknown3": 0,
            }
        ),
    )
    out += _fda_chunk(
        b"@PARAM_SCAN_04",
        fb.param_scan_04_header.build(
            {
                "fixation": 0,
                "mirror_pos": 0,
                "polar": 0,
                "x_dimension_mm": scan_mm,
                "y_dimension_mm": scan_mm,
                "z_resolution_um": axial_um,
                "comp_eff_2": 0.0,
                "comp_eff_3": 0.0,
                "base_pos": 0,
                "used_calib_data": 0,
            }
        ),
    )
    if quality is not None:
        obs = fb.param_obs_02_header.build(
            dict(
                **{
                    "0x1": 0,
                    "0xffff": [0, 0],
                    "camera_model": "SYNTH",
                    "image_quality": quality,
                    "0x300": 0,
                    "0x0": 0,
                    "color_temp": "",
                    "0x2014": 0,
                    "zeros": [0] * 12,
                }
            )
        )
        out += _fda_chunk(b"@PARAM_OBS_02", obs + b"\x00" * (90 - len(obs)))
    if with_contours:
        for lid, top in layers.items():
            if lid in zero_layers:
                seg = np.zeros((n_b, n_a), dtype="<u2")
            else:
                seg = np.flip((img_h - top).round().astype("<u2"), 0)  # el reader hace flip
            out += _fda_chunk(
                b"@CONTOUR_INFO",
                fb.contour_info_header.build(
                    {
                        "id": (layer_ids or {}).get(lid, lid),
                        "method": 0,
                        "format": 0,
                        "width": n_a,
                        "height": n_b,
                        "size": seg.size * 2,
                    }
                )
                + seg.tobytes(),
            )
    path.write_bytes(out + b"\x00")
    return path
