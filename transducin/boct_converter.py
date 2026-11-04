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
from pathlib import Path

from oct_converter.readers.boct import BOCT


def convert_directory(input_dir: str, output_dir: str, glob_patterns: str = "*.OCT") -> tuple[int, int]:
    """Convierte todos los archivos que coincidan con glob_patterns en input_dir a DICOM.

    Args:
        input_dir:     Carpeta raíz de entrada (se recorre recursivamente).
        output_dir:    Carpeta de salida para archivos .dcm.
        glob_patterns: Patrones separados por coma (default: "*.OCT").

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
            reader = BOCT(str(src))
            vol = reader.read_oct_volume()
            out = output_path / f"{src.stem}.dcm"
            vol.save_dicom(out)
            ok += 1
            print(f"OK  {src.name} -> {out.name}")
        except Exception as e:
            fail += 1
            print(f"ERR {src.name}: {e}")

    return ok, fail


def main() -> None:
    p = argparse.ArgumentParser(description="Convertir volúmenes OCT Bioptigen (.OCT) a DICOM estándar")
    p.add_argument("-i", "--input", required=True, help="Carpeta de entrada (recursiva)")
    p.add_argument("-o", "--output", required=True, help="Carpeta de salida para .dcm")
    p.add_argument("--glob", default="*.OCT", help="Patrones separados por coma (default: *.OCT)")
    args = p.parse_args()

    ok, fail = convert_directory(args.input, args.output, args.glob)
    print(f"\nTerminado. Convertidos: {ok}, Fallidos: {fail}. Salida: {args.output}")


if __name__ == "__main__":
    main()
