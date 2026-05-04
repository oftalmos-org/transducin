# deploy/Dockerfile — Transducin para Synology NAS (DSM Container Manager)
#
# Arquitectura objetivo: linux/amd64 (mayoría de NAS Synology x86_64)
# Para NAS ARM64 (e.g. DS923+): usar --platform linux/arm64 en docker build
#
# Build:
#   docker build -f deploy/Dockerfile -t transducin:1.0.0 .
#
# Run en Synology Container Manager:
#   Volumen 1: /data/input/raw → /data/input/CZM (read-only, configure via OCT_BACKUP_PATH)
#   Volumen 2: /data/output
#   Volumen 3: /var/log/transducin
#
# Variables de entorno:
#   ORTHANC_HOST    = <your-orthanc-host>
#   ORTHANC_PORT    = 4242
#   ORTHANC_AET     = ORTHANC
#   TRANSDUCIN_AET  = TRANSDUCIN

FROM python:3.11-slim

# ── System deps ────────────────────────────────────────────────────────────────
# tesseract-ocr: OCR para cirrus_pdf_extractor
# libgl1 / libglib2.0-0: requeridos por opencv-python headless
# libgomp1: requerido por algunas builds de numpy
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-spa \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        git \
    && rm -rf /var/lib/apt/lists/*

# ── Crear usuario sin privilegios ──────────────────────────────────────────────
RUN useradd --system --no-create-home --shell /bin/false transducin

# ── Instalar dependencias Python ──────────────────────────────────────────────
WORKDIR /app

# Copiar solo pyproject.toml primero para cachear pip install
COPY pyproject.toml ./

# Instalar dependencias + opencv-python-headless (sin GUI, más ligero en contenedor)
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        numpy \
        pydicom>=3.0.1 \
        "highdicom>=0.27.0" \
        pynetdicom \
        watchdog \
        Pillow \
        pymupdf \
        pytesseract \
        construct \
        natsort \
        opencv-python-headless \
        scipy \
    && pip install --no-cache-dir pynetdicom

# ── Copiar código fuente ───────────────────────────────────────────────────────
COPY transducin/ ./transducin/

# Instalar el paquete en modo editable
RUN pip install --no-cache-dir -e .

# ── Directorios de datos ───────────────────────────────────────────────────────
RUN mkdir -p /data/input/REVO /data/input/CZM /data/output /var/log/transducin \
    && chown -R transducin:transducin /data /var/log/transducin

USER transducin

# ── Variables de entorno por defecto ──────────────────────────────────────────
ENV ORTHANC_HOST=localhost \
    ORTHANC_PORT=4242 \
    ORTHANC_AET=ORTHANC \
    TRANSDUCIN_AET=TRANSDUCIN \
    PYTHONUNBUFFERED=1

# ── Healthcheck ───────────────────────────────────────────────────────────────
HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import transducin; print('ok')" || exit 1

# ── Entrypoint ────────────────────────────────────────────────────────────────
# Vigila tanto el hotfolder Revo (si está montado) como el directorio CZM del NAS
ENTRYPOINT ["python", "-m", "transducin.hot_folder_watcher"]
CMD ["--watch", "/data/input/REVO", "/data/input/CZM", \
     "--output", "/data/output", \
     "--logs", "/var/log/transducin", \
     "--process-existing"]
