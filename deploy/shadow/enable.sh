#!/bin/bash
# deploy/shadow/enable.sh — instala y activa el modo sombra (macOS/Linux, p. ej. lumen). Idempotente y reversible.
#
#   enable.sh --repo DIR                 simulacro (no cambia nada): comprueba que el parche aplica
#   enable.sh --repo DIR --apply         instala módulos + enganches, crea clave y directorio, añade variables a .env
#   enable.sh --repo DIR --apply --restart   además reinicia el LaunchAgent (necesario para que el proceso cargue el código)
#   enable.sh --repo DIR --rollback      restaura los archivos originales y desactiva
#   touch  $LOG_DIR/DISABLE              apaga el registro AL INSTANTE (sin reiniciar; no toca el procesamiento clínico)
#
# Solo modifica: transducin/{sr_builder,hot_folder_watcher}.py (2 líneas de enganche cada uno), añade dos módulos
# y añade variables TRANSDUCIN_SHADOW_* a .env. No lee .env. Copias de seguridad en DIR/.shadow_backup/.
set -euo pipefail

REPO=""; APPLY=0; RESTART=0; ROLLBACK=0
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../../transducin"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --restart) RESTART=1; shift ;;
    --rollback) ROLLBACK=1; shift ;;
    *) echo "argumento desconocido: $1" >&2; exit 2 ;;
  esac
done
[ -n "$REPO" ] && [ -d "$REPO/transducin" ] || { echo "indica --repo con el checkout de Transducin" >&2; exit 2; }

BK="$REPO/.shadow_backup"
SHADOW_HOME="${TRANSDUCIN_SHADOW_HOME:-$HOME/transducin-shadow}"   # fuera del repositorio
LOG_DIR="$SHADOW_HOME/logs"
KEY="$SHADOW_HOME/hmac.key"
FILES=(sr_builder.py hot_folder_watcher.py)
NEW=(shadow_log.py laterality_evidence.py)
LABEL="org.oftalmos.transducin"

if [ "$ROLLBACK" = 1 ]; then
  for f in "${FILES[@]}"; do [ -f "$BK/$f" ] && cp "$BK/$f" "$REPO/transducin/$f"; done
  for f in "${NEW[@]}"; do rm -f "$REPO/transducin/$f"; done
  touch "$LOG_DIR/DISABLE" 2>/dev/null || true
  echo "rollback hecho. Quita las líneas TRANSDUCIN_SHADOW_* de .env y reinicia el servicio para aplicar."
  exit 0
fi

echo "== comprobando que los enganches aplican (simulacro)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/transducin"
for f in "${FILES[@]}"; do cp "$REPO/transducin/$f" "$TMP/transducin/$f"; done
(cd "$TMP" && patch -p1 --dry-run < "$HERE/hooks.patch") || { echo "el parche no aplica a esta versión" >&2; exit 1; }
for f in "${FILES[@]}"; do grep -q "shadow_log" "$REPO/transducin/$f" && echo "  (ya instalado en $f)"; done

if [ "$APPLY" != 1 ]; then echo "simulacro OK. Repite con --apply."; exit 0; fi

mkdir -p "$BK"
for f in "${FILES[@]}"; do [ -f "$BK/$f" ] || cp "$REPO/transducin/$f" "$BK/$f"; done
if ! grep -q "shadow_log" "$REPO/transducin/sr_builder.py"; then (cd "$REPO" && patch -p1 < "$HERE/hooks.patch"); fi
for f in "${NEW[@]}"; do cp "$SRC/$f" "$REPO/transducin/$f"; done

umask 077
mkdir -p "$LOG_DIR"; chmod 700 "$SHADOW_HOME" "$LOG_DIR"
[ -f "$KEY" ] || { openssl rand -hex 32 > "$KEY"; }
chmod 600 "$KEY"
rm -f "$LOG_DIR/DISABLE"
if [ -f "$REPO/.env" ] && grep -q '^TRANSDUCIN_SHADOW_LOG_DIR=' "$REPO/.env"; then
  echo ".env ya contiene TRANSDUCIN_SHADOW_* (no se modifica)"
else
  { echo ""; echo "# modo sombra (ver deploy/shadow/INSTALL.md)";
    echo "TRANSDUCIN_SHADOW_LOG_DIR=$LOG_DIR";
    echo "TRANSDUCIN_SHADOW_HMAC_KEY_FILE=$KEY";
    echo "TRANSDUCIN_SHADOW_RETENTION_DAYS=30"; } >> "$REPO/.env"
fi
echo "instalado. Clave: $KEY (0600). Log: $LOG_DIR (0700)."

if [ "$RESTART" = 1 ]; then
  launchctl kickstart -k "gui/$(id -u)/$LABEL"
  echo "servicio reiniciado."
else
  echo "NO se reinició el servicio: el código nuevo se carga en el próximo reinicio (use --restart)."
fi
