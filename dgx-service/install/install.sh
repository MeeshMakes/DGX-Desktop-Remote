#!/usr/bin/env bash
# dgx-service/install/install.sh
# One-shot installer for DGX Desktop Remote service on Ubuntu/DGX OS.
# Run as: sudo bash install.sh

set -euo pipefail

SERVICE_NAME="dgx-desktop-remote"
INSTALL_DIR="/opt/dgx-desktop-remote"
SERVICE_USER="${SUDO_USER:-$USER}"
PYTHON="python3"

echo "──────────────────────────────────────────"
echo " DGX Desktop Remote — Service Installer"
echo "──────────────────────────────────────────"

# Root check
if [ "$EUID" -ne 0 ]; then
  echo "❌  Please run as root: sudo bash install.sh"
  exit 1
fi

# 1. System dependencies
echo "[1/6] Installing system packages …"
apt-get update -qq
apt-get install -y -qq xdotool python3-pip python3-venv

# 2. Install dir
echo "[2/6] Setting up ${INSTALL_DIR} …"
mkdir -p "${INSTALL_DIR}"
cp -r "$(dirname "$0")/../src/"* "${INSTALL_DIR}/"

# 3. Python venv
echo "[3/6] Creating Python venv …"
$PYTHON -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install --quiet -r "${INSTALL_DIR}/../requirements.txt"

# 4. Transfer folders (owned by service user)
echo "[4/6] Creating transfer folders for ${SERVICE_USER} …"
TRANSFER_ROOT="/home/${SERVICE_USER}/Desktop/PC-Transfer"
for DIR in inbox outbox staging archive; do
  mkdir -p "${TRANSFER_ROOT}/${DIR}"
  chown "${SERVICE_USER}:${SERVICE_USER}" "${TRANSFER_ROOT}/${DIR}"
done

# 5. Systemd unit
echo "[5/6] Installing systemd service …"
UNIT_SRC="$(dirname "$0")/dgx-desktop-remote.service"
sed "s|__INSTALL_DIR__|${INSTALL_DIR}|g; s|__SERVICE_USER__|${SERVICE_USER}|g" \
    "${UNIT_SRC}" > "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

# 6. Firewall (if ufw active)
if command -v ufw &>/dev/null && ufw status | grep -q "Status: active"; then
  echo "[6/6] Opening UFW ports 22010-22012 …"
  ufw allow 22010:22012/tcp comment 'DGX-Desktop-Remote'
else
  echo "[6/6] UFW not active — skipping firewall rule"
fi

echo ""
echo "✅  Installation complete!"
echo "   Start service:  sudo systemctl start ${SERVICE_NAME}"
echo "   Check status:   sudo systemctl status ${SERVICE_NAME}"
echo "   View logs:      journalctl -u ${SERVICE_NAME} -f"
