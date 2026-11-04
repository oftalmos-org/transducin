# transducin/boct_converter.py
# SPDX-License-Identifier: Apache-2.0
#
# Conversor de volúmenes OCT Bioptigen (.OCT) a DICOM usando oct-converter.
#
# Flujo previsto para Zeiss Cirrus:
#   1. Exportar desde Zeiss Cirrus → ZeissOCTConverter.app → produce .OCT (Bioptigen)
#   2. Este script convierte los .OCT resultantes a .dcm
#
# Formatos soportados por BOCT reader: .OCT  (Bioptigen 4D time-series)
# Nota: los patrones .fds / .img / .vol del --glob por defecto NO son soportados
#       por BOCT; ajusta --glob "*.OCT" salvo que tengas otro reader instalado.
#
# Uso:
#   python -m transducin.boct_converter -i <carpeta_entrada> -o <carpeta_salida>
#   python -m transducin.boct_converter -i ~/Zeiss --glob "*.OCT" -o ~/Output

import argparse
import re
from pathlib import Path

import pydicom
from oct_converter.dicom.dicom import create_dicom_from_boct

# Correcciones propias sobre la salida de oct-converter (sin modificar la librería):
#  1. Lateralidad: BOCT la deduce con re.search(r"O[D|S]", nombre), que acepta "OD"/"OS" dentro de
#     cualquier palabra ("MODEL_OS" -> R, "ROSE_OD" -> L, "POSITION_OD" -> L). Aquí solo vale un
#     token OD/OS delimitado por no-letras, y un valor explícito (`laterality`) manda sobre el nombre.
#  2. Geometría: BOCT escribe PixelSpacing [0.02, 0.02] mm y SliceThickness 0.2 mm como "Placeholder
#     value"; no es calibración. Sin valores explícitos se conserva el DICOM pero se avisa en
#     ImageComments; con valores explícitos se reemplazan.
_LATERALITY_TOKEN = re.compile(r"(?<![A-Za-z])(OD|OS)(?![A-Za-z])", re.IGNORECASE)


def laterality_from_filename(name: str) -> str:
    """'R' | 'L' | '' (desconocida o ambigua) a partir de un token OD/OS delimitado en el nombre."""
    found = {m.group(1).upper() for m in _LATERALITY_TOKEN.finditer(name)}
    if found == {"OD"}:
        return "R"
    if found == {"OS"}:
        return "L"
    return ""


def annotate_boct_dicom(
    path: str | Path,
    *,
    source_name: str = "",
    laterality: str | None = None,
    pixel_spacing_mm: tuple[float, float] | None = None,
    slice_thickness_mm: float | None = None,
) -> list[str]:
    """Corrige/avisa lateralidad y geometría de un DICOM escrito por create_dicom_from_boct.

    Devuelve las notas añadidas a ImageComments.
    """
    ds = pydicom.dcmread(str(path))
    notes: list[str] = []
    if laterality in ("R", "L"):
        lat, src = laterality, "indicada explícitamente"
    else:
        lat = laterality_from_filename(source_name or Path(path).name)
        src = "deducida del nombre del archivo (no está en el .OCT; sin verificar)" if lat else ""
    for tag in ("ImageLaterality", "Laterality"):
        if tag in ds:
            setattr(ds, tag, lat)
    notes.append(f"Lateralidad {lat}: {src}." if lat else "Lateralidad desconocida: el .OCT Bioptigen no la contiene.")
    sfg = ds.get("SharedFunctionalGroupsSequence")
    pm = sfg[0].get("PixelMeasuresSequence") if sfg else None
    if pm:
        item = pm[0]
        if pixel_spacing_mm is not None:
            item.PixelSpacing = [f"{pixel_spacing_mm[0]:g}", f"{pixel_spacing_mm[1]:g}"]
            notes.append(f"PixelSpacing {pixel_spacing_mm[0]:g} x {pixel_spacing_mm[1]:g} mm indicado explícitamente.")
        else:
            notes.append("PixelSpacing SIN CALIBRAR: valor provisional de oct-converter; no usar para medir.")
        if slice_thickness_mm is not None:
            item.SliceThickness = f"{slice_thickness_mm:g}"
            notes.append(f"SliceThickness {slice_thickness_mm:g} mm indicado explícitamente.")
        elif pixel_spacing_mm is None:
            notes.append("SliceThickness SIN CALIBRAR: valor provisional de oct-converter.")
    ds.ImageComments = " ".join(notes)
    ds.save_as(str(path))
    return notes


def convert_directory(
    input_dir: str,
    output_dir: str,
    glob_patterns: str = "*.OCT",
    *,
    laterality: str | None = None,
    pixel_spacing_mm: tuple[float, float] | None = None,
    slice_thickness_mm: float | None = None,
) -> tuple[int, int]:
    """Convierte todos los archivos que coincidan con glob_patterns en input_dir a DICOM.

    Args:
        input_dir:     Carpeta raíz de entrada (se recorre recursivamente).
        output_dir:    Carpeta de salida para archivos .dcm.
        glob_patterns: Patrones separados por coma (default: "*.OCT").
        laterality:    "R" | "L" explícita (manda sobre el nombre del archivo).
        pixel_spacing_mm / slice_thickness_mm: calibración real; sin ella el DICOM se avisa como no calibrado.

    Returns:
        (ok, fail) — número de archivos convertidos y fallidos.
    """
    input_path = Path(input_dir).expanduser().resolve()
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    patterns = [g.strip() for g in glob_patterns.split(",") if g.strip()]
    found = []
    for pat in patterns:
        found.extend(input_path.rglob(pat))

    if not found:
        print(f"No se encontraron archivos con los patrones: {patterns}")
        print("Revisa la ruta y/o ajusta --glob")
        return 0, 0

    ok, fail = 0, 0
    for src in found:
        try:
            # create_dicom_from_boct() lee el 4D .OCT y escribe un DICOM por volumen.
            files = create_dicom_from_boct(str(src), str(output_path))
            for f in files:
                annotate_boct_dicom(
                    f,
                    source_name=src.name,
                    laterality=laterality,
                    pixel_spacing_mm=pixel_spacing_mm,
                    slice_thickness_mm=slice_thickness_mm,
                )
                print(f"OK  {src.name} -> {Path(f).name}")
            ok += 1
        except Exception as e:
            fail += 1
            print(f"ERR {src.name}: {e}")

    return ok, fail


def main() -> None:
    p = argparse.ArgumentParser(description="Convertir volúmenes OCT Bioptigen (.OCT) a DICOM estándar")
    p.add_argument("-i", "--input", required=True, help="Carpeta de entrada (recursiva)")
    p.add_argument("-o", "--output", required=True, help="Carpeta de salida para .dcm")
    p.add_argument("--glob", default="*.OCT", help="Patrones separados por coma (default: *.OCT)")
    p.add_argument("--laterality", choices=("R", "L"), help="lateralidad explícita (si no, token OD/OS del nombre)")
    p.add_argument("--pixel-spacing-mm", nargs=2, type=float, metavar=("FILA", "COL"), help="calibración real en mm")
    p.add_argument("--slice-thickness-mm", type=float, help="separación entre B-scans en mm")
    args = p.parse_args()

    spacing = tuple(args.pixel_spacing_mm) if args.pixel_spacing_mm else None
    ok, fail = convert_directory(
        args.input,
        args.output,
        args.glob,
        laterality=args.laterality,
        pixel_spacing_mm=spacing,
        slice_thickness_mm=args.slice_thickness_mm,
    )
    print(f"\nTerminado. Convertidos: {ok}, Fallidos: {fail}. Salida: {args.output}")


if __name__ == "__main__":
    main()
