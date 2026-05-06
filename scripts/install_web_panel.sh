#!/usr/bin/env bash
set -euo pipefail

# Virtuality Web Panel Installer
# Auto-detects x86_64 / Raspberry Pi ARM64 / Orange Pi 5 ARM64 / generic ARM64 profile.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="${REPO_DIR}/web"
APP_DIR="/opt/virtuality/web"
VENV_DIR="/opt/virtuality/venv"
SERVICE_FILE="/etc/systemd/system/virtuality-web.service"
AUTO_UPDATE_SERVICE_FILE="/etc/systemd/system/virtuality-auto-update.service"
AUTO_UPDATE_TIMER_FILE="/etc/systemd/system/virtuality-auto-update.timer"
PORT="${VIRTUALITY_WEB_PORT:-8088}"
AUTH_USER="${VIRTUALITY_AUTH_USER:-${SUDO_USER:-viktor}}"
LOG_DIR="/var/log/virtuality"
LOG_FILE="${LOG_DIR}/install_web_panel_$(date +%Y%m%d_%H%M%S).log"
PROFILE_DIR="/var/lib/virtuality/config"
PROFILE_FILE="${PROFILE_DIR}/host_profile.json"
SESSION_SECRET_FILE="${PROFILE_DIR}/session_secret"
UPLOAD_TMP_DIR="/var/lib/virtuality/tmp"
TOTAL_STEPS=13
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

mkdir -p "$LOG_DIR" "$PROFILE_DIR" "$UPLOAD_TMP_DIR"
chmod 1777 "$UPLOAD_TMP_DIR"

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }
line() { printf '%*s\n' 72 '' | tr ' ' '─'; }
log() { echo "[$(timestamp)] $*" >> "$LOG_FILE"; }

print_header() {
  clear 2>/dev/null || true
  echo -e "${CYAN}${BOLD}╭────────────────────────────────────────────────────────────╮${RESET}"
  echo -e "${CYAN}${BOLD}│${RESET} ${BOLD}Virtuality Web Panel Installer${RESET}                         ${CYAN}${BOLD}│${RESET}"
  echo -e "${CYAN}${BOLD}│${RESET} Auto profile: x86 / VPS / Raspberry / Orange Pi 5        ${CYAN}${BOLD}│${RESET}"
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
json_value() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "$PROFILE_FILE" "$1" 2>/dev/null || true; }

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

step "Автоматически определяем профиль сборки"
if [[ -x "${REPO_DIR}/scripts/detect_host_profile.sh" ]]; then
  PROFILE="$(${REPO_DIR}/scripts/detect_host_profile.sh 2>>"$LOG_FILE" | tail -n1)"
else
  bash "${REPO_DIR}/scripts/detect_host_profile.sh" >>"$LOG_FILE" 2>&1 || true
  PROFILE="$(json_value profile)"
fi
[[ -n "$PROFILE" ]] || PROFILE="x86_64"
ARCH="$(json_value arch)"
MODEL="$(json_value model)"
LABEL="$(json_value label)"
PACKAGE_PROFILE="$(json_value package_profile)"
ok "Профиль: ${LABEL:-$PROFILE}"
ok "Архитектура: ${ARCH:-unknown}"
ok "Модель: ${MODEL:-unknown}"
ok "Конфиг профиля: $PROFILE_FILE"

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

step "Устанавливаем системные зависимости под выбранную сборку"
COMMON_PACKAGES=(python3 python3-venv python3-pip rsync openssl curl wget unzip nftables novnc python3-websockify)
X86_PACKAGES=(qemu-system-x86 qemu-system-arm qemu-efi-aarch64 virtinst libvirt-daemon-system libvirt-clients bridge-utils cloud-image-utils)
ARM_PACKAGES=(qemu-system-arm qemu-efi-aarch64 virtinst libvirt-daemon-system libvirt-clients bridge-utils cloud-image-utils)
PACKAGES=("${COMMON_PACKAGES[@]}")
case "$PACKAGE_PROFILE" in
  raspberry|orangepi5|arm64)
    PACKAGES+=("${ARM_PACKAGES[@]}")
    ;;
  *)
    PACKAGES+=("${X86_PACKAGES[@]}")
    ;;
esac
run_logged "Системные пакеты установлены для профиля ${PACKAGE_PROFILE:-x86}" apt install -y "${PACKAGES[@]}"
if [[ "${PACKAGE_PROFILE:-x86}" != "raspberry" && "${PACKAGE_PROFILE:-x86}" != "orangepi5" && "${PACKAGE_PROFILE:-x86}" != "arm64" ]]; then
  if command -v qemu-system-aarch64 >/dev/null 2>&1; then
    ok "ARM64 QEMU эмулятор найден: $(command -v qemu-system-aarch64)"
  else
    fail "Не найден qemu-system-aarch64. Установи пакет: sudo apt install -y qemu-system-arm qemu-efi-aarch64"
  fi
fi

step "Копируем web-панель в /opt/virtuality"
run_logged "Создана директория /opt/virtuality" mkdir -p /opt/virtuality
run_logged "Файлы панели синхронизированы в $APP_DIR" rsync -a --delete "$WEB_DIR/" "$APP_DIR/"
if [[ -f "${REPO_DIR}/scripts/patch_web_console.py" ]]; then
  run_logged "noVNC web-console patch применён" python3 "${REPO_DIR}/scripts/patch_web_console.py" "${APP_DIR}/app.py"
else
  warn "patch_web_console.py не найден, noVNC console patch пропущен"
fi
if [[ -f "${REPO_DIR}/scripts/patch_upload_compat.py" ]]; then
  run_logged "upload compatibility patch применён" python3 "${REPO_DIR}/scripts/patch_upload_compat.py" "${APP_DIR}/app.py"
else
  warn "patch_upload_compat.py не найден, совместимость загрузки файлов пропущена"
fi
if [[ -f "${REPO_DIR}/scripts/patch_upload_navigation_guard.py" ]]; then
  run_logged "upload navigation guard patch применён" python3 "${REPO_DIR}/scripts/patch_upload_navigation_guard.py" "${APP_DIR}/app.py"
else
  warn "patch_upload_navigation_guard.py не найден, защита загрузок от переходов пропущена"
fi
if [[ -f "${REPO_DIR}/scripts/patch_disk_images.py" ]]; then
  run_logged "disk images patch применён" python3 "${REPO_DIR}/scripts/patch_disk_images.py" "${APP_DIR}/app.py"
else
  warn "patch_disk_images.py не найден, менеджер дисковых образов пропущен"
fi
if [[ -f "${REPO_DIR}/scripts/patch_vm_boot_order.py" ]]; then
  run_logged "VM boot order patch применён" python3 "${REPO_DIR}/scripts/patch_vm_boot_order.py" "${APP_DIR}/app.py"
else
  warn "patch_vm_boot_order.py не найден, порядок загрузки VM пропущен"
fi
if [[ -f "${REPO_DIR}/scripts/patch_existing_vm_boot_order.py" ]]; then
  run_logged "existing VM boot order patch применён" python3 "${REPO_DIR}/scripts/patch_existing_vm_boot_order.py" "${APP_DIR}/app.py"
else
  warn "patch_existing_vm_boot_order.py не найден, порядок загрузки существующих VM пропущен"
fi
if [[ -f "${REPO_DIR}/scripts/patch_existing_vm_resources.py" ]]; then
  run_logged "existing VM resources patch применён" python3 "${REPO_DIR}/scripts/patch_existing_vm_resources.py" "${APP_DIR}/app.py"
else
  warn "patch_existing_vm_resources.py не найден, ресурсы существующих VM пропущены"
fi
if [[ -f "${REPO_DIR}/scripts/patch_existing_vm_iso_mount.py" ]]; then
  run_logged "existing VM ISO mount patch применён" python3 "${REPO_DIR}/scripts/patch_existing_vm_iso_mount.py" "${APP_DIR}/app.py"
else
  warn "patch_existing_vm_iso_mount.py не найден, монтирование ISO в VM пропущено"
fi
if [[ -f "${REPO_DIR}/scripts/patch_disk_archives.py" ]]; then
  run_logged "disk archive import patch применён" python3 "${REPO_DIR}/scripts/patch_disk_archives.py" "${APP_DIR}/app.py"
else
  warn "patch_disk_archives.py не найден, импорт архивов дисков пропущен"
fi
if [[ -f "${REPO_DIR}/scripts/patch_dhcp_leases_empty.py" ]]; then
  run_logged "DHCP leases empty-state patch применён" python3 "${REPO_DIR}/scripts/patch_dhcp_leases_empty.py" "${APP_DIR}/app.py"
else
  warn "patch_dhcp_leases_empty.py не найден, диагностика DHCP leases пропущена"
fi
if [[ -f "${REPO_DIR}/scripts/patch_network_diagnostics.py" ]]; then
  run_logged "network diagnostics patch применён" python3 "${REPO_DIR}/scripts/patch_network_diagnostics.py" "${APP_DIR}/app.py"
else
  warn "patch_network_diagnostics.py не найден, диагностика сети пропущена"
fi
if [[ -f "${REPO_DIR}/scripts/patch_network_ranges.py" ]]; then
  run_logged "network port ranges patch применён" python3 "${REPO_DIR}/scripts/patch_network_ranges.py" "${APP_DIR}/app.py"
else
  warn "patch_network_ranges.py не найден, поддержка диапазонов портов пропущена"
fi
if [[ -f "${REPO_DIR}/scripts/patch_network_nat_errors.py" ]]; then
  run_logged "network NAT error patch применён" python3 "${REPO_DIR}/scripts/patch_network_nat_errors.py" "${APP_DIR}/app.py"
else
  warn "patch_network_nat_errors.py не найден, безопасные ошибки NAT пропущены"
fi
if [[ -f "${REPO_DIR}/scripts/patch_logs_center.py" ]]; then
  run_logged "logs center patch применён" python3 "${REPO_DIR}/scripts/patch_logs_center.py" "${APP_DIR}/app.py"
else
  warn "patch_logs_center.py не найден, центр журналов пропущен"
fi
if [[ -f "${REPO_DIR}/scripts/patch_vm_network_guard.py" ]]; then
  run_logged "VM network guard patch применён" python3 "${REPO_DIR}/scripts/patch_vm_network_guard.py" "${APP_DIR}/app.py"
else
  warn "patch_vm_network_guard.py не найден, защита от отсутствующего bridge пропущена"
fi
if [[ -f "${REPO_DIR}/scripts/patch_update_center.py" ]]; then
  run_logged "update center patch применён" python3 "${REPO_DIR}/scripts/patch_update_center.py" "${APP_DIR}/app.py"
else
  warn "patch_update_center.py не найден, центр обновлений пропущен"
fi
run_logged "Конфиг профиля доступен web-панели" mkdir -p "$PROFILE_DIR"
if [[ -f "$PROFILE_FILE" ]]; then
  ok "Профиль уже сохранён: $PROFILE_FILE"
else
  warn "Профиль не найден после детекта, будет создан web-панелью при старте"
fi

step "Создаём конфиг авторизации"
if [[ -s "$SESSION_SECRET_FILE" ]]; then
  SESSION_SECRET="$(cat "$SESSION_SECRET_FILE")"
  ok "Используем существующий session secret: $SESSION_SECRET_FILE"
else
  SESSION_SECRET="$(openssl rand -hex 32)"
  printf '%s\n' "$SESSION_SECRET" > "$SESSION_SECRET_FILE"
  chmod 600 "$SESSION_SECRET_FILE"
  ok "Создан постоянный session secret: $SESSION_SECRET_FILE"
fi
cat > "${APP_DIR}/.env" <<EOF
VIRTUALITY_AUTH_USER=${AUTH_USER}
VIRTUALITY_SESSION_SECRET=${SESSION_SECRET}
TMPDIR=${UPLOAD_TMP_DIR}
TEMP=${UPLOAD_TMP_DIR}
TMP=${UPLOAD_TMP_DIR}
EOF
chmod 600 "${APP_DIR}/.env"
ok "Создан ${APP_DIR}/.env"
ok "Временный каталог загрузок: ${UPLOAD_TMP_DIR}"
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
Environment=VIRTUALITY_SOURCE_DIR=${REPO_DIR}
Environment=TMPDIR=${UPLOAD_TMP_DIR}
Environment=TEMP=${UPLOAD_TMP_DIR}
Environment=TMP=${UPLOAD_TMP_DIR}

[Install]
WantedBy=multi-user.target
EOF
ok "Создан service: $SERVICE_FILE"
run_logged "systemd daemon-reload выполнен" systemctl daemon-reload

step "Настраиваем автообновление"
if [[ -f "${REPO_DIR}/scripts/auto_update_check.sh" ]]; then
  chmod +x "${REPO_DIR}/scripts/auto_update_check.sh" || true
  cat > "$AUTO_UPDATE_SERVICE_FILE" <<EOF
[Unit]
Description=Virtuality automatic GitHub update check
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
Group=root
Environment=VIRTUALITY_SOURCE_DIR=${REPO_DIR}
Environment=TMPDIR=${UPLOAD_TMP_DIR}
ExecStart=/bin/bash ${REPO_DIR}/scripts/auto_update_check.sh
EOF
  cat > "$AUTO_UPDATE_TIMER_FILE" <<EOF
[Unit]
Description=Run Virtuality automatic update check daily at 00:00 Moscow time

[Timer]
OnCalendar=*-*-* 21:00:00 UTC
AccuracySec=1min
Persistent=true
Unit=virtuality-auto-update.service

[Install]
WantedBy=timers.target
EOF
  run_logged "systemd daemon-reload выполнен для автообновлений" systemctl daemon-reload
  run_logged "virtuality-auto-update.timer включён" systemctl enable --now virtuality-auto-update.timer
  ok "Автообновление будет проверять GitHub 1 раз в сутки в 00:00 по Москве"
else
  warn "auto_update_check.sh не найден, автообновление пропущено"
fi

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
echo -e "${BOLD}Profile:${RESET}    ${LABEL:-$PROFILE}"
echo -e "${BOLD}Arch:${RESET}       ${ARCH:-unknown}"
echo -e "${BOLD}Service:${RESET}    virtuality-web.service"
echo -e "${BOLD}Auto update:${RESET} virtuality-auto-update.timer / ежедневно в 00:00 по Москве"
echo -e "${BOLD}Upload tmp:${RESET}  ${UPLOAD_TMP_DIR}"
echo -e "${BOLD}Session key:${RESET} ${SESSION_SECRET_FILE}"
echo -e "${BOLD}Status:${RESET}     systemctl status virtuality-web --no-pager"
echo -e "${BOLD}Logs:${RESET}       journalctl -u virtuality-web -f"
echo -e "${BOLD}Install log:${RESET} ${LOG_FILE}"
echo
line
echo -e "${DIM}Следующий шаг: /host, /network, /logs, /update, /vm/create, /vm/NAME/console.${RESET}"
line
