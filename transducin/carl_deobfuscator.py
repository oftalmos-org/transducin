# transducin/carl_deobfuscator.py
#
# Desofuscador de archivos DICOM del Carl Zeiss Meditec (CZM) Cirrus HD-OCT 500.
#
# La ofuscación CZM aplica dos transformaciones al pixel data (JPEG 2000):
#   1. XOR: cada 7° byte (desde offset 0) se hace XOR con 0x5A
#   2. Transposición: el header JP2 (253 bytes) se mueve a ~3/5 del stream
#
# Referencia técnica: https://github.com/pydicom/pydicom/discussions/1618
#
# Uso CLI:
#   python -m transducin.carl_deobfuscator -i <archivo_o_dir> -o <salida>
#   python -m transducin.carl_deobfuscator -i ./DATAFILES/ -o ./output/ -v

from __future__ import annotations

import math
import os
import re
import sys
import argparse
from pathlib import Path

import pydicom
from pydicom.uid import JPEG2000

_uid_re = re.compile(r"^[\d.]+")

# SOP Classes conocidos del Cirrus HD-OCT
_SOP_FUNDUS_8BIT  = "1.2.840.10008.5.1.4.1.1.77.1.5.1"
_SOP_FUNDUS_16BIT = "1.2.840.10008.5.1.4.1.1.77.1.5.2"
_SOP_OCT          = "1.2.840.10008.5.1.4.1.1.77.1.5.4"
_SOP_CZM_BROKEN   = "1.2.840.10008.5.1.4.1.1.12.77"


# ── UIDs ────────────────────────────────────────────────────────────────────

def clean_dicom_uids(ds) -> None:
    """Limpia UIDs corruptos que CZM embebe con basura binaria tras '\\x00'."""
    for elem in ds:
        if elem.VR == "UI" and isinstance(elem.value, str) and "\x00" in elem.value:
            candidate = elem.value.split("\x00")[0].strip()
            m = _uid_re.match(candidate)
            if m:
                elem.value = m.group(0)


# ── Previsualización PNG ─────────────────────────────────────────────────────

def _jp2_to_uint8(frame_bytes: bytes, p_low: float = 2.0, p_high: float = 98.0):
    """Decodifica JP2 y normaliza a uint8 con clipping percentil."""
    try:
        from PIL import Image
        import numpy as np
        import io as _io
        img = Image.open(_io.BytesIO(bytes(frame_bytes)))
        arr = np.array(img).astype(float)
    except Exception:
        return None
    lo = float(np.percentile(arr, p_low))
    hi = float(np.percentile(arr, p_high))
    if hi > lo:
        arr = np.clip((arr - lo) / (hi - lo) * 255.0, 0.0, 255.0).astype("uint8")
    else:
        arr = np.full(arr.shape, 128, dtype="uint8")
    return arr


def compute_dicom_window(jp2_frames: list, n_sample: int = 5) -> tuple[int, int]:
    """Calcula WindowCenter y WindowWidth óptimos (percentiles p2-p98)."""
    try:
        from PIL import Image
        import numpy as np
        import io as _io
    except ImportError:
        return 128, 256

    n = len(jp2_frames)
    if n == 0:
        return 128, 256

    indices = list(range(n)) if n <= n_sample else [
        int(round(i * (n - 1) / (n_sample - 1))) for i in range(n_sample)
    ]
    all_pixels = []
    for idx in indices:
        try:
            img = Image.open(_io.BytesIO(bytes(jp2_frames[idx])))
            all_pixels.append(np.array(img).ravel().astype(float))
        except Exception:
            continue

    if not all_pixels:
        return 128, 256

    flat = np.concatenate(all_pixels)
    lo = float(np.percentile(flat, 2))
    hi = float(np.percentile(flat, 98))
    return int(round((lo + hi) / 2.0)), max(1, int(round(hi - lo)))


def save_preview_png(png_path: Path, jp2_frames: list, verbose: bool = False) -> bool:
    """Genera PNG de previsualización desde frames JP2K.

    Frame central → {stem}.png. Volúmenes multi-frame → {stem}_frames/frame_NNN.png.
    """
    try:
        from PIL import Image
    except ImportError:
        return False

    png_path.parent.mkdir(parents=True, exist_ok=True)
    n = len(jp2_frames)
    arr_mid = _jp2_to_uint8(jp2_frames[n // 2])
    if arr_mid is None:
        return False

    try:
        Image.fromarray(arr_mid).save(str(png_path))
        if verbose:
            label = f"frame {n//2 + 1}/{n}" if n > 1 else "1 frame"
            print(f"  PNG     {png_path.name}  [{label}, {arr_mid.shape[1]}×{arr_mid.shape[0]}px]")
    except Exception:
        return False

    if n > 1:
        frames_dir = png_path.parent / (png_path.stem + "_frames")
        frames_dir.mkdir(exist_ok=True)
        saved = 0
        for i, fb in enumerate(jp2_frames):
            arr = _jp2_to_uint8(fb)
            if arr is None:
                continue
            try:
                Image.fromarray(arr).save(str(frames_dir / f"frame_{i+1:03d}.png"))
                saved += 1
            except Exception:
                pass
        if verbose:
            print(f"  PNG     {frames_dir.name}/  [{saved}/{n} frames]")

    return True


# ── Metadatos de serie ───────────────────────────────────────────────────────

def infer_czm_series_meta(ds) -> tuple[str, str]:
    """Infiere SeriesDescription y Modality para un DICOM del Cirrus HD-OCT 500.

    Usa SOP Class UID y dimensiones (Rows × Columns × NumberOfFrames).
    """
    rows = int(getattr(ds, "Rows", 0))
    cols = int(getattr(ds, "Columns", 0))
    n_frames = 1
    try:
        nf = getattr(ds, "NumberOfFrames", 1)
        n_frames = max(1, int(nf)) if nf else 1
    except (ValueError, TypeError):
        pass

    sop = str(getattr(ds, "SOPClassUID", ""))

    if sop in (_SOP_FUNDUS_8BIT, _SOP_FUNDUS_16BIT):
        if rows > 200 and cols > 200:
            return "Fundus Photo (SLO)", "OP"
        return "OCT En Face", "OPT"

    # Protocolos Macular Cube 512×128
    if cols == 1024 and rows == 512 and n_frames == 128:
        return "Macular Cube 512x128", "OPT"
    if cols == 1024 and rows == 200 and n_frames == 200:
        return "Optic Disc Cube 200x200", "OPT"
    if cols == 1024 and rows == 1024 and n_frames == 5:
        return "HD 5-Line Raster", "OPT"
    if cols == 1024 and n_frames == 21:
        return "HD 21-Line Raster", "OPT"
    if cols == 1024 and n_frames > 1:
        return f"OCT Cube {rows}x{n_frames}", "OPT"
    if n_frames == 1 and rows <= 200 and cols >= 256:
        return "OCT En Face", "OPT"
    if n_frames == 1 and cols == 1024:
        return "HD Single Line", "OPT"

    return "OCT B-Scan", "OPT"


# ── Algoritmo de desofuscación CZM ──────────────────────────────────────────

def unscramble_czm(frame: bytes) -> bytearray:
    """Revierte la ofuscación CZM sobre un frame JPEG 2000.

    Pasos:
      1. Deshacer XOR (cada 7° byte XOR 0x5A)
      2. Deshacer transposición (header JP2 de 253 bytes movido a floor(len/5*3))
    """
    frame = bytearray(frame)

    for ii in range(0, len(frame), 7):
        frame[ii] ^= 0x5A

    jp2_sig_full  = b"\x00\x00\x00\x0C\x6A\x50\x20\x20"
    jp2_sig_short = b"\x00\x00\x00\x0C"

    offset = frame.find(jp2_sig_full)
    if offset == -1:
        offset = frame.find(jp2_sig_short)
    if offset == -1:
        raise ValueError("No se encontró la firma JP2 en el frame después del XOR.")

    jp2_offset = offset
    d = bytearray()
    d.extend(frame[jp2_offset : jp2_offset + 253])
    d.extend(frame[993:1016])
    d.extend(frame[276:763])
    d.extend(frame[23:276])
    d.extend(frame[1016:jp2_offset])
    d.extend(frame[:23])
    d.extend(frame[763:993])
    d.extend(frame[jp2_offset + 253:])

    if len(d) != len(frame):
        raise ValueError(
            f"Error en la reconstrucción: longitud resultante {len(d)} "
            f"≠ longitud original {len(frame)}"
        )
    return d


def is_obfuscated(frame: bytes) -> bool:
    """Detecta si un frame tiene la ofuscación CZM activa (heurística)."""
    if len(frame) < 16:
        return False

    jp2_sig_full = b"\x00\x00\x00\x0C\x6A\x50\x20\x20"

    if frame[:8] == jp2_sig_full:
        return False
    if frame[:2] == b"\xFF\x4F":
        return False

    test = bytearray(frame)
    for ii in range(0, min(len(test), math.floor(len(frame) / 5 * 3) + 32), 7):
        test[ii] ^= 0x5A

    offset = test.find(jp2_sig_full)
    if offset == -1:
        offset = test.find(b"\x00\x00\x00\x0C")
    return offset is not None and offset > 100


# ── Procesamiento de un archivo ──────────────────────────────────────────────

def process_dicom_file(
    input_path: Path,
    output_path: Path,
    verbose: bool = False,
    save_png: bool = True,
    write_dcm: bool = True,
) -> bool:
    """Desofusca un archivo DICOM CZM y lo guarda como DICOM estándar.

    Args:
        input_path:  Ruta al .DCM original.
        output_path: Ruta de salida para el .dcm desofuscado.
        verbose:     Mostrar detalles por frame.
        save_png:    Generar PNG de previsualización junto al .dcm.
        write_dcm:   Escribir el .dcm de salida (False = solo PNG).

    Returns:
        True si el procesamiento fue exitoso.
    """
    try:
        ds = pydicom.dcmread(str(input_path), force=True)
    except Exception as e:
        print(f"  [ERROR] No se pudo leer {input_path.name}: {e}")
        return False

    if "PixelData" not in ds:
        print(f"  [SKIP]  {input_path.name} — sin PixelData")
        return True

    pixel_data = ds.PixelData
    from pydicom.encaps import decode_data_sequence, encapsulate

    try:
        frames_raw = decode_data_sequence(pixel_data)
        if not frames_raw:
            raise ValueError("no encapsulado")
        if verbose:
            print(f"  Pixel data encapsulado: {len(frames_raw)} frame(s)")
    except Exception:
        frames_raw = [pixel_data]
        if verbose:
            print(f"  Pixel data no encapsulado, tamaño: {len(pixel_data)} bytes")

    if frames_raw and not is_obfuscated(frames_raw[0]):
        print(f"  [INFO]  {input_path.name} — no ofuscado, copiando sin cambios")
        if input_path != output_path:
            import shutil
            shutil.copy2(input_path, output_path)
        return True

    processed_frames = []
    errors = 0
    for i, frame in enumerate(frames_raw):
        try:
            processed_frames.append(bytes(unscramble_czm(frame)))
            if verbose:
                print(f"  Frame {i+1}/{len(frames_raw)} — OK")
        except Exception as e:
            print(f"  [WARN]  Frame {i+1} — error: {e}")
            processed_frames.append(frame)
            errors += 1

    clean_dicom_uids(ds)

    if "NumberOfFrames" in ds and isinstance(ds.NumberOfFrames, str) and "\x00" in ds.NumberOfFrames:
        ds.NumberOfFrames = int(ds.NumberOfFrames.split("\x00")[0].strip())

    ds.PixelData = encapsulate(processed_frames)
    ds.is_implicit_VR = False
    ds["PixelData"].is_undefined_length = True

    from pydicom.dataset import FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, JPEG2000Lossless

    if not hasattr(ds, "file_meta") or ds.file_meta is None:
        ds.file_meta = FileMetaDataset()

    orig_ts = getattr(ds.file_meta, "TransferSyntaxUID", None)
    ds.file_meta.TransferSyntaxUID = orig_ts if orig_ts in (JPEG2000, JPEG2000Lossless) else JPEG2000Lossless

    clean_dicom_uids(ds.file_meta)

    sop_uid = str(getattr(ds, "SOPClassUID", ""))
    if sop_uid == _SOP_CZM_BROKEN:
        ds.SOPClassUID = _SOP_OCT
        if verbose:
            print(f"  SOP corregido → {_SOP_OCT}")

    ds.file_meta.MediaStorageSOPClassUID    = getattr(ds, "SOPClassUID", _SOP_OCT)
    ds.file_meta.MediaStorageSOPInstanceUID = getattr(ds, "SOPInstanceUID", pydicom.uid.generate_uid())
    ds.file_meta.ImplementationClassUID     = "1.2.3.4"
    ds.is_implicit_VR   = False
    ds.is_little_endian = True
    ds.PixelRepresentation = 0  # JP2K del Cirrus es siempre unsigned

    series_desc, modality = infer_czm_series_meta(ds)
    if not str(getattr(ds, "SeriesDescription", "")).strip():
        ds.SeriesDescription = series_desc
    ds.Modality = modality

    wc, ww = compute_dicom_window(processed_frames)
    ds.WindowCenter = wc
    ds.WindowWidth  = ww

    if verbose:
        print(f"  Serie/Modalidad → {series_desc} / {modality}")
        print(f"  Window C/W      → {wc} / {ww}")

    stem = output_path.name.split(".")[0]
    output_path = output_path.parent / (stem + ".dcm")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if write_dcm:
        try:
            pydicom.dcmwrite(str(output_path), ds, write_like_original=False)
        except Exception as e:
            print(f"  [ERROR] No se pudo guardar {output_path}: {e}")
            return False

    if save_png:
        save_preview_png(output_path.with_suffix(".png"), processed_frames, verbose=verbose)

    status = "OK" if errors == 0 else f"OK con {errors} frame(s) con error"
    print(f"  [DONE]  {input_path.name} → {output_path.name}  [{status}]")
    return True


# ── Procesamiento por lotes ──────────────────────────────────────────────────

def process_directory(
    input_dir: Path,
    output_dir: Path,
    recursive: bool = True,
    verbose: bool = False,
    save_png: bool = True,
) -> tuple[int, int]:
    """Desofusca todos los .DCM encontrados en input_dir.

    Returns:
        (ok, err)
    """
    pattern = "**/*.DCM" if recursive else "*.DCM"
    files = list({f.resolve() for f in list(input_dir.glob(pattern)) + list(input_dir.glob(pattern.lower()))})

    if not files:
        candidates = [f for f in input_dir.glob("**/*" if recursive else "*") if f.is_file() and not f.suffix]
        for c in candidates:
            try:
                with open(c, "rb") as fh:
                    fh.seek(128)
                    if fh.read(4) == b"DICM":
                        files.append(c)
            except Exception:
                pass

    files.sort()
    if not files:
        print(f"No se encontraron archivos DICOM en: {input_dir}")
        return 0, 0

    print(f"\nArchivos DICOM encontrados: {len(files)}")
    print("-" * 60)

    ok = err = 0
    for fp in files:
        rel  = fp.relative_to(input_dir)
        stem = fp.name.split(".")[0]
        out  = output_dir / rel.parent / (stem + ".dcm")
        print(f"Procesando: {rel}")
        if process_dicom_file(fp, out, verbose=verbose, save_png=save_png):
            ok += 1
        else:
            err += 1

    return ok, err


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Desofusca DICOMs CZM (Cirrus HD-OCT). Revierte XOR + transposición JP2K.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python -m transducin.carl_deobfuscator -i archivo.DCM -o salida.dcm
  python -m transducin.carl_deobfuscator -i ./DATAFILES/ -o ./output/ -v
  python -m transducin.carl_deobfuscator -i ./DATAFILES/ -o ./output/ --no-png
        """,
    )
    parser.add_argument("-i", "--input",  required=True, help="Archivo .DCM o directorio de entrada")
    parser.add_argument("-o", "--output", required=True, help="Archivo .DCM o directorio de salida")
    parser.add_argument("--overwrite",    action="store_true", help="Sobreescribir archivos existentes")
    parser.add_argument("--no-recursive", action="store_true", help="No buscar en subdirectorios")
    parser.add_argument("-v", "--verbose", action="store_true", help="Información detallada por frame")
    parser.add_argument("--no-png",       action="store_true", help="No generar PNG de previsualización")
    args = parser.parse_args()

    input_path  = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    if not input_path.exists():
        print(f"ERROR: ruta de entrada no existe: {input_path}")
        sys.exit(1)

    print("=" * 60)
    print("  Transducin — CZM Deobfuscator (Cirrus HD-OCT)")
    print("=" * 60)

    if input_path.is_file():
        if output_path.exists() and not args.overwrite:
            print(f"ERROR: el archivo de salida ya existe. Usa --overwrite.")
            sys.exit(1)
        ok = process_dicom_file(input_path, output_path, verbose=args.verbose, save_png=not args.no_png)
        sys.exit(0 if ok else 1)

    if input_path.is_dir():
        output_path.mkdir(parents=True, exist_ok=True)
        ok, err = process_directory(
            input_path, output_path,
            recursive=not args.no_recursive,
            verbose=args.verbose,
            save_png=not args.no_png,
        )
        print("\n" + "=" * 60)
        print(f"  Resumen: {ok} OK, {err} con error")
        print("=" * 60)
        sys.exit(0 if err == 0 else 1)

    print(f"ERROR: la entrada no es archivo ni directorio: {input_path}")
    sys.exit(1)


if __name__ == "__main__":
    main()
