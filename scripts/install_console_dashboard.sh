#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASH_SRC="${REPO_DIR}/scripts/virtuality_console_dashboard.sh"
DASH_DST="/usr/local/bin/virtuality-console-dashboard"
SERVICE_FILE="/etc/systemd/system/virtuality-console-dashboard.service"
TARGET_USER="${SUDO_USER:-${USER:-root}}"

if [[ "$EUID" -ne 0 ]]; then
  echo "Ошибка: запусти через sudo: sudo bash scripts/install_console_dashboard.sh"
  exit 1
fi

if [[ ! -f "$DASH_SRC" ]]; then
  echo "Ошибка: не найден файл $DASH_SRC"
  exit 1
fi

apt update
apt install -y btop procps iproute2 util-linux coreutils

install -m 0755 "$DASH_SRC" "$DASH_DST"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Virtuality console dashboard on tty1
After=multi-user.target network-online.target libvirtd.service cockpit.socket
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
Type=simple
ExecStart=/usr/local/bin/virtuality-console-dashboard
Restart=always
RestartSec=2
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
Environment=TERM=linux
Environment=VIRTUALITY_DASHBOARD_INTERVAL=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl disable --now getty@tty1.service >/dev/null 2>&1 || true
systemctl enable --now virtuality-console-dashboard.service

USER_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6 || true)"
if [[ -n "$USER_HOME" && -d "$USER_HOME" ]]; then
  BASHRC="$USER_HOME/.bashrc"
  touch "$BASHRC"
  grep -q "alias bt=" "$BASHRC" || cat >> "$BASHRC" <<'EOF'

# Virtuality quick aliases
alias bt='btop'
alias dash='sudo systemctl restart virtuality-console-dashboard && sudo chvt 1'
alias dash-stop='sudo systemctl stop virtuality-console-dashboard'
alias dash-start='sudo systemctl start virtuality-console-dashboard'
alias dash-status='systemctl status virtuality-console-dashboard --no-pager'
alias vcheck='cd ~/virtuality && sudo bash scripts/check_node.sh'
EOF
  chown "$TARGET_USER:$TARGET_USER" "$BASHRC" || true
fi

echo "============================================================"
echo "Virtuality console dashboard installed"
echo "============================================================"
echo "Физический монитор: tty1"
echo "Перейти на дашборд: sudo chvt 1"
echo "Команда btop: bt"
echo "Перезапустить дашборд: dash"
echo "Остановить дашборд: dash-stop"
echo "Запустить дашборд: dash-start"
echo "Статус: dash-status"
echo "============================================================"
