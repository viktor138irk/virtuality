#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="${REPO_DIR}/web"
APP_DIR="/opt/virtuality/web"
VENV_DIR="/opt/virtuality/venv"
SERVICE_FILE="/etc/systemd/system/virtuality-web.service"
PORT="${VIRTUALITY_WEB_PORT:-8088}"

if [[ "$EUID" -ne 0 ]]; then
  echo "Ошибка: запусти через sudo: sudo bash scripts/install_web_panel.sh"
  exit 1
fi

if [[ ! -d "$WEB_DIR" ]]; then
  echo "Ошибка: web directory not found: $WEB_DIR"
  exit 1
fi

apt update
apt install -y python3 python3-venv python3-pip rsync

mkdir -p /opt/virtuality
rsync -a --delete "$WEB_DIR/" "$APP_DIR/"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Virtuality Web Panel
After=network-online.target libvirtd.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
ExecStart=${VENV_DIR}/bin/uvicorn app:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=3
User=root
Group=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now virtuality-web.service

if command -v ufw >/dev/null 2>&1; then
  ufw allow "${PORT}/tcp" || true
fi

SERVER_IP="$(hostname -I | awk '{print $1}')"

echo "============================================================"
echo "Virtuality Web Panel installed"
echo "============================================================"
echo "URL: http://${SERVER_IP}:${PORT}"
echo "Service: virtuality-web.service"
echo "Status: systemctl status virtuality-web --no-pager"
echo "Logs: journalctl -u virtuality-web -f"
echo "============================================================"
