# SPDX-License-Identifier: Apache-2.0
"""
inspect_exam_ai_res.py
Inspecciona el bloque EXAM_AI_RES de un archivo .OPT de Optopol.
Usa el parser real del repo (parse_opt_chunks).
"""
import sys
import zlib
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from transducin.revo_opt_reader import parse_opt_chunks, _decompress_block

def inspect_exam_ai_res(opt_path: str):
    path = Path(opt_path)
    print(f"\n{'='*60}")
    print(f"Archivo: {path.name}")
    print(f"Tamaño: {path.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"{'='*60}\n")

    with open(path, 'rb') as f:
        data = f.read()

    chunks = parse_opt_chunks(data)

    # Listar todos los chunks encontrados
    print("CHUNKS ENCONTRADOS:")
    for name, info in sorted(chunks.items()):
        print(f"  {name:20s}: {info['real_size']:>12,} bytes  (meta=0x{info['meta']:08x}, field2=0x{info['field2']:08x})")

    print(f"\nTotal: {len(chunks)} chunks\n")

    # Buscar EXAM_AI_RES
    target = None
    for key in chunks:
        if 'EXAM_AI_RES' in key or 'AI_RES' in key or 'AI' in key:
            target = key
            break

    if target is None:
        # Buscar cualquier chunk con EXAM
        exam_chunks = [k for k in chunks if 'EXAM' in k.upper()]
        if exam_chunks:
            print(f"No se encontró EXAM_AI_RES, pero hay chunks EXAM: {exam_chunks}")
            target = exam_chunks[0]
        else:
            print("No se encontró EXAM_AI_RES ni chunks EXAM.")
            return

    c = chunks[target]
    raw = data[c["offset"]: c["offset"] + c["real_size"]]
    print(f"Chunk '{target}': {c['real_size']:,} bytes")
    print(f"  meta=0x{c['meta']:08x}, field2=0x{c['field2']:08x}")
    print("  Primeros 64 bytes (hex):")

    for i in range(0, min(64, len(raw)), 16):
        hex_part = ' '.join(f'{b:02x}' for b in raw[i:i+16])
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw[i:i+16])
        print(f"    {i:04x}: {hex_part:<48}  {ascii_part}")

    # ¿Son todos ceros?
    if raw == b'\x00' * len(raw):
        print(f"\n  >>> CONTENIDO: {len(raw)} bytes de ceros (vacío/reservado)")
        return

    # Intentar descompresión con el decoder del repo
    print("\nIntentando _decompress_block (prefijo tipo+size)...")
    dec = _decompress_block(raw)
    if dec:
        print(f"  Descompresión exitosa: {len(dec):,} bytes")
        print("  Primeros 200 bytes:")
        try:
            text = dec[:200].decode('utf-8', errors='replace')
            print(f"    {text}")
        except Exception:
            pass
        print("  Primeros 64 bytes (hex):")
        for i in range(0, min(64, len(dec)), 16):
            hex_part = ' '.join(f'{b:02x}' for b in dec[i:i+16])
            print(f"    {i:04x}: {hex_part}")

        # Intentar JSON
        try:
            data_json = json.loads(dec)
            print("\n  FORMATO: JSON válido")
            print(f"  Contenido: {json.dumps(data_json, indent=2, ensure_ascii=False)[:2000]}")
            out_path = path.parent / f"{path.stem}_EXAM_AI_RES.json"
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(data_json, f, indent=2, ensure_ascii=False)
            print(f"\n  JSON guardado en: {out_path}")
        except json.JSONDecodeError:
            print("\n  FORMATO: No es JSON")
            out_path = path.parent / f"{path.stem}_EXAM_AI_RES.bin"
            with open(out_path, 'wb') as f:
                f.write(dec)
            print(f"  Datos crudos guardados en: {out_path}")
    else:
        print("  _decompress_block falló")

        # Intentar zlib directo
        print("\nIntentando zlib.decompress directo...")
        try:
            dec2 = zlib.decompress(raw)
            print(f"  zlib directo exitoso: {len(dec2):,} bytes")
            print(f"  Primeros 200 bytes: {dec2[:200]}")
        except zlib.error as e:
            print(f"  zlib directo falló: {e}")

        print(f"\n  Datos raw sin descomprimir ({len(raw)} bytes):")
        print(f"  Todo ceros: {all(b == 0 for b in raw)}")
        non_zero = sum(1 for b in raw if b != 0)
        print(f"  Bytes no-cero: {non_zero} de {len(raw)}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        import glob
        opts = glob.glob(r'C:\SOCT_DATA\**\*.opt', recursive=True)
        if opts:
            opts.sort(key=lambda x: Path(x).stat().st_size, reverse=True)
            inspect_exam_ai_res(opts[0])
        else:
            print("No se encontraron archivos .OPT en C:\\SOCT_DATA")
    else:
        inspect_exam_ai_res(sys.argv[1])
