#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Virtuality One-command Bootstrap Installer
# Public install entrypoint for clean Ubuntu/Debian servers.
# ==========================================================

REPO_URL="${VIRTUALITY_REPO_URL:-https://github.com/viktor138irk/virtuality.git}"
DEFAULT_INSTALL_USER="${SUDO_USER:-root}"
INSTALL_USER="${VIRTUALITY_USER:-$DEFAULT_INSTALL_USER}"
PROJECT_BASE_DIR="${VIRTUALITY_PROJECT_BASE_DIR:-/opt/virtuality}"
PROJECT_DIR="${VIRTUALITY_PROJECT_DIR:-${PROJECT_BASE_DIR}/source}"
RUN_BRIDGE="${VIRTUALITY_SETUP_BRIDGE:-0}"
BRIDGE_IFACE="${VIRTUALITY_BRIDGE_IFACE:-}"
RUN_TEST_VM="${VIRTUALITY_CREATE_TEST_VM:-0}"
WEB_PORT="${VIRTUALITY_WEB_PORT:-8088}"
LOG_DIR="/var/log/virtuality"
LOG_FILE="${LOG_DIR}/bootstrap_$(date +%Y%m%d_%H%M%S).log"
MIN_ROOT_FREE_MB="${VIRTUALITY_MIN_ROOT_FREE_MB:-8192}"
MIN_VAR_FREE_MB="${VIRTUALITY_MIN_VAR_FREE_MB:-20480}"
MIN_RAM_MB="${VIRTUALITY_MIN_RAM_MB:-4096}"
MIN_CPU_CORES="${VIRTUALITY_MIN_CPU_CORES:-2}"
SKIP_REQUIREMENTS="${VIRTUALITY_SKIP_REQUIREMENTS:-0}"

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

run_logged() {
  local desc="$1"
  shift
  local start_ts pid code spinner elapsed symbol i
  log "RUN: $*"
  start_ts="$(date +%s)"
  "$@" >> "$LOG_FILE" 2>&1 &
  pid=$!
  spinner=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
  i=0
  while kill -0 "$pid" 2>/dev/null; do
    elapsed=$(( $(date +%s) - start_ts ))
    symbol="${spinner[$((i % ${#spinner[@]}))]}"
    printf "\r  ${CYAN}%s${RESET} %s... ${GRAY}%ss${RESET} ${GRAY}(лог: %s)${RESET}" "$symbol" "$desc" "$elapsed" "$LOG_FILE"
    sleep 1
    i=$((i + 1))
  done
  wait "$pid"
  code=$?
  printf "\r%*s\r" 120 ""
  if [[ "$code" -eq 0 ]]; then
    ok "$desc"
  else
    fail "$desc"
  fi
}

header() {
  clear 2>/dev/null || true
  echo -e "${CYAN}${BOLD}╭────────────────────────────────────────────────────────────╮${RESET}"
  echo -e "${CYAN}${BOLD}│${RESET} ${BOLD}Virtuality Bootstrap Installer${RESET}                         ${CYAN}${BOLD}│${RESET}"
  echo -e "${CYAN}${BOLD}│${RESET} One-command KVM / libvirt / Cockpit / Web Panel setup     ${CYAN}${BOLD}│${RESET}"
  echo -e "${CYAN}${BOLD}╰────────────────────────────────────────────────────────────╯${RESET}"
  echo
  echo -e "${GRAY}Repository:${RESET} ${REPO_URL}"
  echo -e "${GRAY}Auth/install user:${RESET} ${INSTALL_USER}"
  echo -e "${GRAY}Project dir:${RESET} ${PROJECT_DIR}"
  echo -e "${GRAY}Log:${RESET} ${LOG_FILE}"
  echo
}

require_root() {
  [[ "$EUID" -eq 0 ]] || fail "Запусти от root: curl ... | sudo bash"
}

free_mb_for_path() {
  local path="$1"
  mkdir -p "$path" 2>/dev/null || true
  df -Pm "$path" | awk 'NR==2 {print $4}'
}

check_password_state() {
  local user="$1"
  local hash
  hash="$(getent shadow "$user" | cut -d: -f2 || true)"
  if [[ -z "$hash" || "$hash" == "!" || "$hash" == "*" || "$hash" == "!!" ]]; then
    warn "У пользователя ${user} пароль не задан или заблокирован. Для входа в панель выполни: passwd ${user}"
  else
    ok "У пользователя ${user} есть пароль для входа в панель"
  fi
}

check_requirements() {
  step "Проверяем минимальные системные требования"
  if [[ "$SKIP_REQUIREMENTS" == "1" ]]; then
    warn "Проверка требований отключена через VIRTUALITY_SKIP_REQUIREMENTS=1"
    return 0
  fi

  local root_free var_free ram_mb cpu_cores
  root_free="$(free_mb_for_path /)"
  var_free="$(free_mb_for_path /var/lib 2>/dev/null || free_mb_for_path /)"
  ram_mb="$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)"
  cpu_cores="$(nproc 2>/dev/null || echo 1)"

  if (( root_free >= MIN_ROOT_FREE_MB )); then
    ok "Свободно на /: ${root_free} MB минимум ${MIN_ROOT_FREE_MB} MB"
  else
    fail "Недостаточно места на /: ${root_free} MB. Нужно минимум ${MIN_ROOT_FREE_MB} MB. Очисти диск: apt clean; journalctl --vacuum-time=3d"
  fi

  if (( var_free >= MIN_VAR_FREE_MB )); then
    ok "Свободно для /var/lib: ${var_free} MB минимум ${MIN_VAR_FREE_MB} MB"
  else
    fail "Недостаточно места для VM-хранилища /var/lib: ${var_free} MB. Нужно минимум ${MIN_VAR_FREE_MB} MB"
  fi

  if (( ram_mb >= MIN_RAM_MB )); then
    ok "RAM: ${ram_mb} MB минимум ${MIN_RAM_MB} MB"
  else
    warn "RAM: ${ram_mb} MB меньше рекомендуемых ${MIN_RAM_MB} MB. Установка возможна, но VM будут ограничены"
  fi

  if (( cpu_cores >= MIN_CPU_CORES )); then
    ok "CPU cores: ${cpu_cores} минимум ${MIN_CPU_CORES}"
  else
    warn "CPU cores: ${cpu_cores} меньше рекомендуемых ${MIN_CPU_CORES}"
  fi

  if grep -E -q '(vmx|svm)' /proc/cpuinfo; then
    ok "CPU virtualization vmx/svm найдена"
  else
    warn "CPU virtualization vmx/svm не найдена. Проверь BIOS/UEFI или настройки VPS"
  fi
}

ensure_user() {
  if [[ "$INSTALL_USER" == "root" ]]; then
    ok "Пользователь авторизации: root"
    check_password_state root
    return 0
  fi

  if id "$INSTALL_USER" >/dev/null 2>&1; then
    ok "Пользователь уже существует: ${INSTALL_USER}"
  else
    run_logged "Создан пользователь ${INSTALL_USER}" adduser --disabled-password --gecos "" "$INSTALL_USER"
    warn "Пароль для ${INSTALL_USER} не задан. После установки выполни: sudo passwd ${INSTALL_USER}"
  fi
  usermod -aG sudo "$INSTALL_USER" >> "$LOG_FILE" 2>&1 || true
  ok "Пользователь ${INSTALL_USER} добавлен в sudo"
  check_password_state "$INSTALL_USER"
}

run_as_user() {
  if [[ "$INSTALL_USER" == "root" ]]; then
    bash -lc "$*"
  else
    sudo -H -u "$INSTALL_USER" bash -lc "$*"
  fi
}

prepare_project_dir() {
  mkdir -p "$PROJECT_BASE_DIR"
  if [[ "$INSTALL_USER" == "root" ]]; then
    chown root:root "$PROJECT_BASE_DIR"
  else
    chown "$INSTALL_USER:$INSTALL_USER" "$PROJECT_BASE_DIR"
  fi
  chmod 755 "$PROJECT_BASE_DIR"
  ok "Рабочая директория подготовлена: ${PROJECT_BASE_DIR}"
}

header

step "Проверяем root и apt"
require_root
command -v apt >/dev/null 2>&1 || fail "apt не найден. Нужен Debian/Ubuntu"
ok "root подтверждён"
ok "apt найден"

check_requirements

step "Устанавливаем базовые пакеты"
run_logged "apt update выполнен" apt update
run_logged "Установлены sudo/git/curl/ca-certificates" apt install -y sudo git curl ca-certificates openssh-client nano htop

step "Создаём или проверяем пользователя"
ensure_user

step "Готовим рабочую директорию в /opt"
prepare_project_dir

step "Проверяем место после подготовки пользователя"
root_free_after="$(free_mb_for_path /)"
if (( root_free_after < MIN_ROOT_FREE_MB )); then
  fail "После подготовки пользователя свободного места стало мало: ${root_free_after} MB на /. Очисти диск и повтори установку"
fi
ok "Место после подготовки пользователя: ${root_free_after} MB на /"

step "Клонируем или обновляем репозиторий"
if [[ -d "$PROJECT_DIR/.git" ]]; then
  run_logged "Репозиторий обновлён" run_as_user "cd '$PROJECT_DIR' && git reset --hard origin/main && git pull"
else
  run_logged "Репозиторий склонирован" run_as_user "cd '$PROJECT_BASE_DIR' && git clone '$REPO_URL' source"
fi

step "Запускаем основной установщик ноды"
run_logged "Virtuality Node установлен" bash "$PROJECT_DIR/install_virtuality_node.sh"

step "Устанавливаем healthcheck-команду"
run_logged "Healthcheck установлен" bash "$PROJECT_DIR/scripts/install_healthcheck_command.sh"

step "Устанавливаем консольный dashboard"
run_logged "Console dashboard установлен" bash "$PROJECT_DIR/scripts/install_console_dashboard.sh"

step "Устанавливаем web-панель"
VIRTUALITY_AUTH_USER="$INSTALL_USER" VIRTUALITY_WEB_PORT="$WEB_PORT" bash "$PROJECT_DIR/scripts/install_web_panel.sh" >> "$LOG_FILE" 2>&1 &
web_pid=$!
web_start="$(date +%s)"
web_i=0
web_spinner=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
while kill -0 "$web_pid" 2>/dev/null; do
  web_elapsed=$(( $(date +%s) - web_start ))
  printf "\r  ${CYAN}%s${RESET} Web-панель устанавливается... ${GRAY}%ss${RESET} ${GRAY}(лог: %s)${RESET}" "${web_spinner[$((web_i % ${#web_spinner[@]}))]}" "$web_elapsed" "$LOG_FILE"
  sleep 1
  web_i=$((web_i + 1))
done
wait "$web_pid"
web_code=$?
printf "\r%*s\r" 120 ""
[[ "$web_code" -eq 0 ]] && ok "Web-панель установлена" || fail "Web-панель не установлена"

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
  run_logged "Тестовая VM создана" bash "$PROJECT_DIR/scripts/create_test_vm.sh" || warn "Тестовая VM не создана. Проверь ISO/bridge/log"
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
echo "  passwd ${INSTALL_USER}        # если пароль ещё не задан или заблокирован"
echo "  sudo vhealth"
echo "  cd ${PROJECT_DIR}"
echo "  ip -br a"
echo
echo -e "${YELLOW}Важно:${RESET} bridge br0 по умолчанию не включается в one-command режиме, чтобы не уронить SSH."
echo "Для bridge: cd ${PROJECT_DIR} && sudo bash scripts/setup_bridge_br0.sh INTERFACE static"
echo
