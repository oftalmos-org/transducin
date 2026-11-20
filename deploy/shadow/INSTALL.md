# Modo sombra — instalación escalonada (lumen y HP Z2)

El modo sombra **solo observa**: no cambia lateralidad, mediciones, SR ni webhooks. Está **desactivado** hasta que el proceso tenga
`TRANSDUCIN_SHADOW_LOG_DIR` y una clave HMAC. Un fallo del log nunca interrumpe el procesamiento.

## Hallazgo previo al despliegue (verificado 2026-11-04)

| Nodo | Qué ejecuta Transducin | ¿Emite SR/webhook? |
|---|---|---|
| **lumen** | Solo la API (`python -m transducin.api`, puerto 8004), sin el *hot folder watcher*; `api.py` no llama a `build_sr` ni al webhook | **No** por esa vía: activar el log allí recogería ~0 registros (sí cubriría `retroactive_sr.py` si se ejecutara allí) |
| **HP Z2** | NSSM con el *hot folder watcher* (Revo/Cirrus/Spectralis/Topcon) | **Sí**: es donde están los datos reales |

Por eso la etapa 1 es una **validación en lumen sin tocar su árbol de producción** (hecha: copia aislada + intérprete del nodo, ver el
informe) y la **activación con datos reales es en HP Z2**. Lumen queda listo con `enable.sh` si se quiere cubrir `retroactive_sr`.

## Comprobaciones de cada etapa (todas deben cumplirse antes de pasar a la siguiente)

1. Registro fuera de Git, directorio `0700` y archivos `0600` (en Windows: ACL solo para la cuenta del servicio).
2. Clave HMAC en archivo propio con acceso restringido; **no** en el repositorio ni en `.env` versionado.
3. Sin información identificable ni valores clínicos en el log (`grep` de un NOEL ID/nombre de prueba; el canario lo hace).
4. SR y payload del webhook idénticos con el log activo y apagado (el canario lo hace con un SR sintético).
5. Sin errores en `logs/transducin-*.log` relacionados con `shadow_log`, sin registros duplicados anómalos y sin degradación
   (medido en lumen: ≈0,2 ms por registro sin archivo que leer; `build_sr` completo ≈27 ms).
6. Interruptor probado: `touch <LOG_DIR>/DISABLE` detiene el registro sin reiniciar.

## lumen (macOS)

```bash
cd ~/Developer/RetinaOS/Transducin
bash deploy/shadow/enable.sh --repo .                 # simulacro: solo comprueba que el parche aplica
bash deploy/shadow/enable.sh --repo . --apply         # instala (con copia en .shadow_backup/), NO reinicia
python deploy/shadow/canary_shadow.py "$PWD"          # canario sintético (usa el intérprete del nodo)
bash deploy/shadow/enable.sh --repo . --apply --restart   # cuando lo decidas: recarga el LaunchAgent
bash deploy/shadow/enable.sh --repo . --rollback      # deshace
```

(`deploy/shadow/*` y los dos módulos deben estar en el árbol de lumen: copiarlos desde el commit local; no hay push.)

## HP Z2 (Windows + NSSM) — manual, desde PowerShell del propio equipo

```powershell
cd <ruta de Transducin>
# 1. Copiar a transducin\ : shadow_log.py y laterality_evidence.py (del commit local)
# 2. Enganches (2 líneas por archivo): comprobar y aplicar
git apply --check deploy\shadow\hooks.patch
git apply deploy\shadow\hooks.patch
# 3. Clave y directorio fuera del repositorio, solo para la cuenta del servicio
$home_ = "C:\ProgramData\transducin-shadow"; New-Item -ItemType Directory -Force "$home_\logs" | Out-Null
python -c "import secrets; print(secrets.token_hex(32))" | Set-Content -NoNewline "$home_\hmac.key"
icacls $home_ /inheritance:r /grant:r "<CUENTA_DEL_SERVICIO>:(OI)(CI)F" "Administrators:(OI)(CI)F"
# 4. Variables del servicio NSSM (no requieren tocar el repositorio)
nssm set <NombreServicio> AppEnvironmentExtra +TRANSDUCIN_SHADOW_LOG_DIR=$home_\logs +TRANSDUCIN_SHADOW_HMAC_KEY_FILE=$home_\hmac.key +TRANSDUCIN_SHADOW_RETENTION_DAYS=30
# 5. Reiniciar el servicio (ventana sin estudios en cola) y subir un estudio sintético; revisar "$home_\logs"
nssm restart <NombreServicio>
# Apagado inmediato sin reiniciar:   New-Item "$home_\logs\DISABLE" -ItemType File
# Rollback: git apply -R deploy\shadow\hooks.patch ; eliminar los 2 módulos ; nssm reset <NombreServicio> AppEnvironmentExtra ; reiniciar
```

Notas: en Windows no se puede verificar la ACL desde Python, por eso se omite la comprobación de bits POSIX de la clave (la protección es la ACL
del paso 3). Los archivos de Revo grandes se omiten para la lectura de OCTPARAMS por encima de `TRANSDUCIN_SHADOW_MAX_READ_MB` (400 por defecto).

## Informe

Pasados unos días con estudios reales (en el nodo, local):

```bash
PYTHONPATH=. python scripts/shadow_report.py --md informe.md log <LOG_DIR>
```

Solo contiene cuentas. Si una ruta aparece con pocos estudios o ninguno, el informe lo indica como *no ejercitada* / *datos insuficientes*.
