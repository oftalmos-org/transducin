@echo off
REM ============================================================================
REM  Transducin Windows Service Setup (NSSM) — production workstation
REM
REM  Registers TransducinRevoWatcher and TransducinPTSWatcher.
REM  Run as Administrator.
REM
REM  NOTE: TransducinHotFolder (multivendor: Cirrus .DCM, Spectralis .E2E,
REM  Topcon .FDA/.FDS, Bioptigen .OCT, Cirrus PDF) is NOT registered on this
REM  machine because those file sources are not present on production workstation.
REM  To register it on another node:
REM    %NSSM% install TransducinHotFolder "%PYTHON%"
REM    %NSSM% set TransducinHotFolder AppParameters "-m transducin.hot_folder_watcher --watch <DIR>"
REM    %NSSM% set TransducinHotFolder AppDirectory "%WORKDIR%"
REM    (see hot_folder_watcher.py --help for full CLI options)
REM ============================================================================

setlocal

set NSSM_DIR=C:\transducin\tools\nssm
set NSSM=%NSSM_DIR%\nssm.exe
set PYTHON=C:\Python311\python.exe
set WORKDIR=C:\transducin
set LOGDIR=%WORKDIR%\logs

REM ── Check admin ────────────────────────────────────────────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Run this script as Administrator.
    pause
    exit /b 1
)

REM ── Download NSSM if not present ───────────────────────────────────────────
set NSSM_URL=https://nssm.cc/release/nssm-2.24.zip
set NSSM_ZIP=%NSSM_DIR%\nssm.zip

if not exist "%NSSM%" (
    echo NSSM not found. Downloading...
    if not exist "%NSSM_DIR%" mkdir "%NSSM_DIR%"

    powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%NSSM_URL%' -OutFile '%NSSM_ZIP%'"
    if not exist "%NSSM_ZIP%" (
        echo ERROR: Failed to download NSSM.
        pause
        exit /b 1
    )

    powershell -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::ExtractToDirectory('%NSSM_ZIP%', '%NSSM_DIR%')"

    REM Move nssm.exe from nested folder to NSSM_DIR
    if exist "%NSSM_DIR%\nssm-2.24\win64\nssm.exe" (
        copy "%NSSM_DIR%\nssm-2.24\win64\nssm.exe" "%NSSM%" >nul
    ) else if exist "%NSSM_DIR%\nssm-2.24\win32\nssm.exe" (
        copy "%NSSM_DIR%\nssm-2.24\win32\nssm.exe" "%NSSM%" >nul
    )

    del "%NSSM_ZIP%" 2>nul

    if not exist "%NSSM%" (
        echo ERROR: NSSM extraction failed.
        pause
        exit /b 1
    )
    echo NSSM installed: %NSSM%
)

REM ── Create log directory ───────────────────────────────────────────────────
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM ── Unregister TransducinHotFolder if present (not used on production workstation) ──────────
%NSSM% stop TransducinHotFolder >nul 2>&1
%NSSM% remove TransducinHotFolder confirm >nul 2>&1

REM ============================================================================
REM  Service 1: TransducinRevoWatcher
REM  Revo FC130 hot folder watcher (continuous watchdog mode)
REM ============================================================================
echo.
echo === TransducinRevoWatcher ===

%NSSM% stop TransducinRevoWatcher >nul 2>&1
%NSSM% remove TransducinRevoWatcher confirm >nul 2>&1

%NSSM% install TransducinRevoWatcher "%PYTHON%"
%NSSM% set TransducinRevoWatcher AppParameters "-m transducin.revo_watcher"
%NSSM% set TransducinRevoWatcher AppDirectory "%WORKDIR%"

REM Logging
%NSSM% set TransducinRevoWatcher AppStdout "%LOGDIR%\revo_service.log"
%NSSM% set TransducinRevoWatcher AppStderr "%LOGDIR%\revo_service.log"
%NSSM% set TransducinRevoWatcher AppStdoutCreationDisposition 4
%NSSM% set TransducinRevoWatcher AppStderrCreationDisposition 4
%NSSM% set TransducinRevoWatcher AppRotateFiles 1
%NSSM% set TransducinRevoWatcher AppRotateBytes 10485760

REM Auto-restart on failure
%NSSM% set TransducinRevoWatcher AppExit Default Restart
%NSSM% set TransducinRevoWatcher AppRestartDelay 5000

REM Start type: Automatic
%NSSM% set TransducinRevoWatcher Start SERVICE_AUTO_START

REM Description
%NSSM% set TransducinRevoWatcher DisplayName "Transducin Revo FC130 Watcher"
%NSSM% set TransducinRevoWatcher Description "Monitors REVO_WATCH_FOLDER for .OPT files, converts to DICOM + SR, uploads to Orthanc via REST API."

echo TransducinRevoWatcher registered.

REM ============================================================================
REM  Service 2: TransducinPTSWatcher
REM  PTS 925Wi Orthanc polling service
REM ============================================================================
echo.
echo === TransducinPTSWatcher ===

%NSSM% stop TransducinPTSWatcher >nul 2>&1
%NSSM% remove TransducinPTSWatcher confirm >nul 2>&1

%NSSM% install TransducinPTSWatcher "%PYTHON%"
%NSSM% set TransducinPTSWatcher AppParameters "-m transducin.pts925_watcher"
%NSSM% set TransducinPTSWatcher AppDirectory "%WORKDIR%"

REM Logging
%NSSM% set TransducinPTSWatcher AppStdout "%LOGDIR%\pts925_service.log"
%NSSM% set TransducinPTSWatcher AppStderr "%LOGDIR%\pts925_service.log"
%NSSM% set TransducinPTSWatcher AppStdoutCreationDisposition 4
%NSSM% set TransducinPTSWatcher AppStderrCreationDisposition 4
%NSSM% set TransducinPTSWatcher AppRotateFiles 1
%NSSM% set TransducinPTSWatcher AppRotateBytes 10485760

REM Auto-restart on failure
%NSSM% set TransducinPTSWatcher AppExit Default Restart
%NSSM% set TransducinPTSWatcher AppRestartDelay 5000

REM Start type: Automatic
%NSSM% set TransducinPTSWatcher Start SERVICE_AUTO_START

REM Description
%NSSM% set TransducinPTSWatcher DisplayName "Transducin PTS 925Wi Watcher"
%NSSM% set TransducinPTSWatcher Description "Polls Orthanc for OPV studies without paired SR, generates VF SR TID 1500, uploads back to Orthanc."

echo TransducinPTSWatcher registered.

REM ============================================================================
REM  Start services
REM ============================================================================
echo.
echo === Starting services ===

net start TransducinRevoWatcher
net start TransducinPTSWatcher

echo.
echo === Status ===
%NSSM% status TransducinRevoWatcher
%NSSM% status TransducinPTSWatcher

echo.
echo To stop:
echo   net stop TransducinRevoWatcher
echo   net stop TransducinPTSWatcher
echo.
echo To remove:
echo   %NSSM% remove TransducinRevoWatcher confirm
echo   %NSSM% remove TransducinPTSWatcher confirm
echo.
pause
