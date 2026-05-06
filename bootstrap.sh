#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Virtuality One-command Bootstrap Installer
# Public install entrypoint for clean Ubuntu/Debian servers.
# ==========================================================

REPO_URL="${VIRTUALITY_REPO_URL:-https://github.com/viktor138irk/virtuality.git}"
INSTALL_USER="${VIRTUALITY_USER:-viktor}"
INSTALL_HOME="/home/${INSTALL_USER}"
PROJECT_DIR="${INSTALL_HOME}/virtuality"
RUN_BRIDGE="${VIRTUALITY_SETUP_BRIDGE:-0}"
BRIDGE_IFACE="${VIRTUALITY_BRIDGE_IFACE:-}"
RUN_TEST_VM="${VIRTUALITY_CREATE_TEST_VM:-0}"
WEB_PORT="${VIRTUALITY_WEB_PORT:-8088}"
LOG_DIR="/var/log/virtuality"
LOG_FILE="${LOG_DIR}/bootstrap_$(date +%Y%m%d_%H%M%S).log"

ESC="\033"
RESET="${ESC}[0m"
BOLD="${ESC}[1m"
GREEN="${ESC}[32m"
YELLOW="${ESC}[33m"
RED="${ESC}[31m"
CYAN="${ESC}[36m"
BLUE="${ESC}[34m"
GRAY="${ESC}[90m"

mkdir -p "$LOG_DIR"

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(timestamp)] $*" >> "$LOG_FILE"; }
ok() { echo -e "  ${GREEN}✓${RESET} $*"; log "OK: $*"; }
warn() { echo -e "  ${YELLOW}!${RESET} $*"; log "WARN: $*"; }
fail() { echo -e "  ${RED}✗${RESET} $*"; log "ERROR: $*"; echo; echo -e "${RED}${BOLD}Bootstrap остановлен.${RESET} Лог: ${LOG_FILE}"; exit 1; }
step() { echo; echo -e "${BLUE}${BOLD}▶${RESET} ${BOLD}$*${RESET}"; log "STEP: $*"; }
run_logged() { local desc="$1"; shift; log "RUN: $*"; if "$@" >> "$LOG_FILE" 2>&1; then ok "$desc"; else fail "$desc"; fi; }

header() {
  clear 2>/dev/null || true
  echo -e "${CYAN}${BOLD}╭────────────────────────────────────────────────────────────╮${RESET}"
  echo -e "${CYAN}${BOLD}│${RESET} ${BOLD}Virtuality Bootstrap Installer${RESET}                         ${CYAN}${BOLD}│${RESET}"
  echo -e "${CYAN}${BOLD}│${RESET} One-command KVM / libvirt / Cockpit / Web Panel setup     ${CYAN}${BOLD}│${RESET}"
  echo -e "${CYAN}${BOLD}╰────────────────────────────────────────────────────────────╯${RESET}"
  echo
  echo -e "${GRAY}Repository:${RESET} ${REPO_URL}"
  echo -e "${GRAY}Install user:${RESET} ${INSTALL_USER}"
  echo -e "${GRAY}Project dir:${RESET} ${PROJECT_DIR}"
  echo -e "${GRAY}Log:${RESET} ${LOG_FILE}"
  echo
}

require_root() {
  [[ "$EUID" -eq 0 ]] || fail "Запусти от root: curl ... | sudo bash"
}

ensure_user() {
  if id "$INSTALL_USER" >/dev/null 2>&1; then
    ok "Пользователь уже существует: ${INSTALL_USER}"
  else
    run_logged "Создан пользователь ${INSTALL_USER}" adduser --disabled-password --gecos "" "$INSTALL_USER"
    warn "Пароль для ${INSTALL_USER} не задан. После установки выполни: sudo passwd ${INSTALL_USER}"
  fi
  usermod -aG sudo "$INSTALL_USER" >> "$LOG_FILE" 2>&1 || true
  ok "Пользователь ${INSTALL_USER} добавлен в sudo"
}

run_as_user() {
  sudo -H -u "$INSTALL_USER" bash -lc "$*"
}

header

step "Проверяем root и apt"
require_root
command -v apt >/dev/null 2>&1 || fail "apt не найден. Нужен Debian/Ubuntu"
ok "root подтверждён"
ok "apt найден"

step "Устанавливаем базовые пакеты"
run_logged "apt update выполнен" apt update
run_logged "Установлены sudo/git/curl/ca-certificates" apt install -y sudo git curl ca-certificates openssh-client nano htop

step "Создаём или проверяем пользователя"
ensure_user

step "Клонируем или обновляем репозиторий"
if [[ -d "$PROJECT_DIR/.git" ]]; then
  run_logged "Репозиторий обновлён" run_as_user "cd '$PROJECT_DIR' && git reset --hard origin/main && git pull"
else
  run_logged "Репозиторий склонирован" run_as_user "cd '$INSTALL_HOME' && git clone '$REPO_URL' virtuality"
fi

step "Запускаем основной установщик ноды"
run_logged "Virtuality Node установлен" bash "$PROJECT_DIR/install_virtuality_node.sh"

step "Устанавливаем healthcheck-команду"
run_logged "Healthcheck установлен" bash "$PROJECT_DIR/scripts/install_healthcheck_command.sh"

step "Устанавливаем консольный dashboard"
run_logged "Console dashboard установлен" bash "$PROJECT_DIR/scripts/install_console_dashboard.sh"

step "Устанавливаем web-панель"
VIRTUALITY_AUTH_USER="$INSTALL_USER" VIRTUALITY_WEB_PORT="$WEB_PORT" bash "$PROJECT_DIR/scripts/install_web_panel.sh" >> "$LOG_FILE" 2>&1 && ok "Web-панель установлена" || fail "Web-панель не установлена"

if [[ "$RUN_BRIDGE" == "1" ]]; then
  step "Настраиваем bridge br0"
  if [[ -z "$BRIDGE_IFACE" ]]; then
    BRIDGE_IFACE="$(ip route | awk '/default/ {print $5; exit}')"
  fi
  if [[ -z "$BRIDGE_IFACE" ]]; then
    warn "Не удалось определить интерфейс для br0. Пропускаю bridge setup"
  else
    warn "Bridge меняет сеть. Если SSH зависнет, нужен физический доступ или консоль провайдера."
    bash "$PROJECT_DIR/scripts/setup_bridge_br0.sh" "$BRIDGE_IFACE" static >> "$LOG_FILE" 2>&1 && ok "Bridge br0 настроен на ${BRIDGE_IFACE}" || warn "Bridge setup завершился с предупреждением. Проверь лог"
  fi
else
  warn "Bridge br0 не настраивался автоматически. Запуск вручную: sudo bash scripts/setup_bridge_br0.sh INTERFACE static"
fi

if [[ "$RUN_TEST_VM" == "1" ]]; then
  step "Создаём тестовую VM"
  bash "$PROJECT_DIR/scripts/create_test_vm.sh" >> "$LOG_FILE" 2>&1 && ok "Тестовая VM создана" || warn "Тестовая VM не создана. Проверь ISO/bridge/log"
fi

step "Финальная диагностика"
if command -v vhealth >/dev/null 2>&1; then
  vhealth >> "$LOG_FILE" 2>&1 && ok "vhealth: OK" || warn "vhealth вернул warnings/errors. Проверь: sudo vhealth"
else
  warn "Команда vhealth не найдена"
fi

SERVER_IP="$(hostname -I | awk '{print $1}')"

echo
echo -e "${GREEN}${BOLD}╭────────────────────────────────────────────────────────────╮${RESET}"
echo -e "${GREEN}${BOLD}│${RESET} ${BOLD}Virtuality Bootstrap завершён${RESET}                            ${GREEN}${BOLD}│${RESET}"
echo -e "${GREEN}${BOLD}╰────────────────────────────────────────────────────────────╯${RESET}"
echo
echo -e "${BOLD}Cockpit:${RESET}       https://${SERVER_IP}:9090"
echo -e "${BOLD}Web Panel:${RESET}     http://${SERVER_IP}:${WEB_PORT}"
echo -e "${BOLD}Login:${RESET}         ${INSTALL_USER} / пароль Linux-пользователя"
echo -e "${BOLD}Project:${RESET}       ${PROJECT_DIR}"
echo -e "${BOLD}Log:${RESET}           ${LOG_FILE}"
echo
echo -e "${BOLD}После установки:${RESET}"
echo "  sudo passwd ${INSTALL_USER}        # если пароль ещё не задан"
echo "  sudo vhealth"
echo "  cd ${PROJECT_DIR}"
echo "  ip -br a"
echo
echo -e "${YELLOW}Важно:${RESET} bridge br0 по умолчанию не включается в one-command режиме, чтобы не уронить SSH."
echo "Для bridge: cd ${PROJECT_DIR} && sudo bash scripts/setup_bridge_br0.sh INTERFACE static"
echo
