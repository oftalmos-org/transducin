#!/bin/bash
# deploy/start_transducin.sh — arranca la API de Transducin (puerto 8004) en lumen.
# Lo invoca el LaunchAgent org.oftalmos.transducin (ver deploy/org.oftalmos.transducin.plist).
# Carga .env (nunca se hardcodean IPs ni credenciales) y lanza `python -m transducin.api`.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
exec .venv314/bin/python -m transducin.api
