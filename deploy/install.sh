#!/bin/bash
# deploy/install.sh — Instalación de Transducin en Oracle Cloud Linux 9 / RHEL 9
# Ejecutar como root: bash deploy/install.sh
set -euo pipefail

INSTALL_DIR=/opt/transducin
DATA_DIR=/data
WATCH_DIR=${DATA_DIR}/input/REVO
OUTPUT_DIR=${DATA_DIR}/output
LOG_DIR=/var/log/transducin
SERVICE_USER=transducin

echo "=== Transducin — Instalación en Oracle Linux 9 ==="

# 1. Python 3.11 via DNF (disponible en OL9)
echo "[1/6] Instalando Python 3.11..."
dnf install -y python3.11 python3.11-pip python3.11-devel gcc

# 2. Crear usuario de servicio
echo "[2/6] Creando usuario '${SERVICE_USER}'..."
useradd --system --no-create-home --shell /sbin/nologin ${SERVICE_USER} 2>/dev/null || true

# 3. Crear directorios
echo "[3/6] Creando directorios..."
mkdir -p ${INSTALL_DIR} ${WATCH_DIR} ${OUTPUT_DIR}/images ${OUTPUT_DIR}/sr ${LOG_DIR}
chown -R ${SERVICE_USER}:${SERVICE_USER} ${INSTALL_DIR} ${DATA_DIR} ${LOG_DIR}

# 4. Clonar/copiar código y crear venv
echo "[4/6] Instalando código Transducin..."
if [ -d "${INSTALL_DIR}/.git" ]; then
    git -C ${INSTALL_DIR} pull
else
    git clone https://github.com/oftalmos-org/transducin.git ${INSTALL_DIR}
fi

python3.11 -m venv ${INSTALL_DIR}/venv
${INSTALL_DIR}/venv/bin/pip install --upgrade pip
${INSTALL_DIR}/venv/bin/pip install -e ${INSTALL_DIR}

# 5. Instalar servicio systemd
echo "[5/6] Instalando servicio systemd..."
cp ${INSTALL_DIR}/deploy/transducin.service /etc/systemd/system/transducin.service

# Configurar rutas en el unit file
sed -i "s|Environment=\"WATCH_DIR=.*\"|Environment=\"WATCH_DIR=${WATCH_DIR}\"|" \
    /etc/systemd/system/transducin.service
sed -i "s|Environment=\"OUTPUT_DIR=.*\"|Environment=\"OUTPUT_DIR=${OUTPUT_DIR}\"|" \
    /etc/systemd/system/transducin.service

systemctl daemon-reload
systemctl enable transducin.service

# 6. Verificar
echo "[6/6] Verificando instalación..."
${INSTALL_DIR}/venv/bin/python -c "import transducin; print('transducin OK')"
${INSTALL_DIR}/venv/bin/python -c "import pydicom; print('pydicom', pydicom.__version__)"
${INSTALL_DIR}/venv/bin/python -c "import highdicom; print('highdicom', highdicom.__version__)"

echo ""
echo "=== Instalación completa ==="
echo ""
echo "Configurar Orthanc en /etc/systemd/system/transducin.service:"
echo "  Environment=\"ORTHANC_HOST=<IP_ORTHANC>\""
echo "  Environment=\"ORTHANC_PORT=4242\""
echo ""
echo "Luego iniciar el servicio:"
echo "  systemctl start transducin"
echo "  journalctl -fu transducin"
