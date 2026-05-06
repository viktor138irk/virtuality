#!/usr/bin/env bash
set -euo pipefail

# Virtuality Web Panel Installer
# Clean step-by-step installer with readable statuses, Linux-user auth and log file.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="${REPO_DIR}/web"
APP_DIR="/opt/virtuality/web"
VENV_DIR="/opt/virtuality/venv"
SERVICE_FILE="/etc/systemd/system/virtuality-web.service"
PORT="${VIRTUALITY_WEB_PORT:-8088}"
AUTH_USER="${VIRTUALITY_AUTH_USER:-${SUDO_USER:-viktor}}"
LOG_DIR="/var/log/virtuality"
LOG_FILE="${LOG_DIR}/install_web_panel_$(date +%Y%m%d_%H%M%S).log"
TOTAL_STEPS=11
CURRENT_STEP=0

ESC="\033"
RESET="${ESC}[0m"
BOLD="${ESC}[1m"
DIM="${ESC}[2m"
GREEN="${ESC}[32m"
YELLOW="${ESC}[33m"
RED="${ESC}[31m"
CYAN="${ESC}[36m"
BLUE="${ESC}[34m"
GRAY="${ESC}[90m"

mkdir -p "$LOG_DIR"

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }
line() { printf '%*s\n' 72 '' | tr ' ' '─'; }
log() { echo "[$(timestamp)] $*" >> "$LOG_FILE"; }

print_header() {
  clear 2>/dev/null || true
  echo -e "${CYAN}${BOLD}╭────────────────────────────────────────────────────────────╮${RESET}"
  echo -e "${CYAN}${BOLD}│${RESET} ${BOLD}Virtuality Web Panel Installer${RESET}                         ${CYAN}${BOLD}│${RESET}"
  echo -e "${CYAN}${BOLD}│${RESET} Панель управления VM, сервисами, сетью и storage pools     ${CYAN}${BOLD}│${RESET}"
  echo -e "${CYAN}${BOLD}╰────────────────────────────────────────────────────────────╯${RESET}"
  echo
  echo -e "${GRAY}Лог установки:${RESET} ${LOG_FILE}"
  echo -e "${GRAY}Порт панели:${RESET} ${PORT}"
  echo -e "${GRAY}Linux auth user:${RESET} ${AUTH_USER}"
  echo
}

step() {
  CURRENT_STEP=$((CURRENT_STEP + 1))
  echo
  echo -e "${BLUE}${BOLD}[${CURRENT_STEP}/${TOTAL_STEPS}]${RESET} ${BOLD}$*${RESET}"
  log "STEP ${CURRENT_STEP}/${TOTAL_STEPS}: $*"
}

ok() { echo -e "  ${GREEN}✓${RESET} $*"; log "OK: $*"; }
warn() { echo -e "  ${YELLOW}!${RESET} $*"; log "WARN: $*"; }
fail() { echo -e "  ${RED}✗${RESET} $*"; log "ERROR: $*"; echo; echo -e "${RED}${BOLD}Установка остановлена.${RESET} Подробности в логе: ${LOG_FILE}"; exit 1; }

run_logged() {
  local description="$1"
  shift
  log "RUN: $*"
  if "$@" >> "$LOG_FILE" 2>&1; then
    ok "$description"
  else
    fail "$description"
  fi
}

service_state() { systemctl is-active "$1" 2>/dev/null || echo "inactive"; }
require_root() { [[ "$EUID" -eq 0 ]] || fail "Запусти через sudo: sudo bash scripts/install_web_panel.sh"; }

print_header

step "Проверяем права и структуру проекта"
require_root
ok "Права root подтверждены"
[[ -d "$WEB_DIR" ]] || fail "Не найдена директория web: $WEB_DIR"
ok "Исходники web найдены: $WEB_DIR"
[[ -f "$WEB_DIR/app.py" ]] || fail "Не найден web/app.py"
ok "FastAPI entrypoint найден: web/app.py"
[[ -f "$WEB_DIR/requirements.txt" ]] || fail "Не найден web/requirements.txt"
ok "requirements.txt найден"

step "Проверяем Linux-пользователя для входа"
if id "$AUTH_USER" >/dev/null 2>&1; then
  ok "Пользователь найден: $AUTH_USER"
else
  fail "Пользователь $AUTH_USER не найден. Создай его или передай VIRTUALITY_AUTH_USER=username"
fi
if getent shadow "$AUTH_USER" | cut -d: -f2 | grep -Eq '^(!|\*|!!)?$'; then
  warn "У пользователя $AUTH_USER может быть заблокирован пароль. Установи пароль: sudo passwd $AUTH_USER"
else
  ok "У пользователя $AUTH_USER есть пароль для Linux-auth"
fi

step "Проверяем базовые команды"
for cmd in apt systemctl rsync python3 hostname openssl; do
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "$cmd найден: $(command -v "$cmd")"
  else
    fail "$cmd не найден"
  fi
done

step "Обновляем apt cache"
run_logged "apt update выполнен" apt update

step "Устанавливаем системные зависимости"
run_logged "Пакеты Python/venv/pip/rsync установлены" apt install -y python3 python3-venv python3-pip rsync openssl

step "Копируем web-панель в /opt/virtuality"
run_logged "Создана директория /opt/virtuality" mkdir -p /opt/virtuality
run_logged "Файлы панели синхронизированы в $APP_DIR" rsync -a --delete "$WEB_DIR/" "$APP_DIR/"

step "Создаём конфиг авторизации"
SESSION_SECRET="$(openssl rand -hex 32)"
cat > "${APP_DIR}/.env" <<EOF
VIRTUALITY_AUTH_USER=${AUTH_USER}
VIRTUALITY_SESSION_SECRET=${SESSION_SECRET}
EOF
chmod 600 "${APP_DIR}/.env"
ok "Создан ${APP_DIR}/.env"
ok "Вход будет по Linux-пользователю: ${AUTH_USER}"

step "Создаём Python virtualenv"
if [[ -d "$VENV_DIR" ]]; then
  warn "Virtualenv уже существует, будет переиспользован: $VENV_DIR"
else
  run_logged "Virtualenv создан: $VENV_DIR" python3 -m venv "$VENV_DIR"
fi

step "Устанавливаем Python-зависимости"
run_logged "pip обновлён" "$VENV_DIR/bin/pip" install --upgrade pip
run_logged "Python-зависимости установлены" "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

step "Создаём systemd service"
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
Environment=VIRTUALITY_AUTH_USER=${AUTH_USER}

[Install]
WantedBy=multi-user.target
EOF
ok "Создан service: $SERVICE_FILE"
run_logged "systemd daemon-reload выполнен" systemctl daemon-reload

step "Запускаем Virtuality Web Panel"
run_logged "virtuality-web.service включён и запущен" systemctl enable --now virtuality-web.service
sleep 2
STATE="$(service_state virtuality-web.service)"
if [[ "$STATE" == "active" ]]; then
  ok "virtuality-web.service active"
else
  warn "virtuality-web.service состояние: $STATE"
  journalctl -u virtuality-web.service -n 40 --no-pager >> "$LOG_FILE" 2>&1 || true
fi

step "Настраиваем firewall и проверяем порт"
if command -v ufw >/dev/null 2>&1; then
  ufw allow "${PORT}/tcp" >> "$LOG_FILE" 2>&1 || warn "Не удалось добавить UFW правило для ${PORT}/tcp"
  ok "UFW правило добавлено/проверено: ${PORT}/tcp"
else
  warn "ufw не установлен, firewall-правило пропущено"
fi
if command -v ss >/dev/null 2>&1 && ss -tulpn | grep -q ":${PORT}"; then
  ok "Порт ${PORT} слушается"
else
  warn "Порт ${PORT} пока не найден в ss; проверь service logs"
fi

SERVER_IP="$(hostname -I | awk '{print $1}')"

echo
echo -e "${GREEN}${BOLD}╭────────────────────────────────────────────────────────────╮${RESET}"
echo -e "${GREEN}${BOLD}│${RESET} ${BOLD}Установка Virtuality Web Panel завершена${RESET}                 ${GREEN}${BOLD}│${RESET}"
echo -e "${GREEN}${BOLD}╰────────────────────────────────────────────────────────────╯${RESET}"
echo
echo -e "${BOLD}URL:${RESET}        http://${SERVER_IP}:${PORT}"
echo -e "${BOLD}Login:${RESET}      ${AUTH_USER} / пароль Linux-пользователя"
echo -e "${BOLD}Service:${RESET}    virtuality-web.service"
echo -e "${BOLD}Status:${RESET}     systemctl status virtuality-web --no-pager"
echo -e "${BOLD}Logs:${RESET}       journalctl -u virtuality-web -f"
echo -e "${BOLD}Install log:${RESET} ${LOG_FILE}"
echo
line
echo -e "${DIM}Следующий шаг: страница создания VM из интерфейса.${RESET}"
line
