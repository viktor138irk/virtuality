#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Virtuality Node Installer
# Clean step-by-step installer for KVM/QEMU/libvirt/Cockpit
# ==========================================================

PROJECT_NAME="Virtuality"
PROJECT_DIR="/opt/virtuality"
STORAGE_DIR="/var/lib/virtuality"
ISO_DIR="${STORAGE_DIR}/iso"
IMAGES_DIR="${STORAGE_DIR}/images"
BACKUP_DIR="${STORAGE_DIR}/backups"
LOG_DIR="/var/log/virtuality"
COCKPIT_PORT="9090"
LOG_FILE="${LOG_DIR}/install_node_$(date +%Y%m%d_%H%M%S).log"
TOTAL_STEPS=13
CURRENT_STEP=0
MIN_ROOT_FREE_MB="${VIRTUALITY_MIN_ROOT_FREE_MB:-8192}"
MIN_VAR_FREE_MB="${VIRTUALITY_MIN_VAR_FREE_MB:-20480}"
MIN_RAM_MB="${VIRTUALITY_MIN_RAM_MB:-4096}"
MIN_CPU_CORES="${VIRTUALITY_MIN_CPU_CORES:-2}"
SKIP_REQUIREMENTS="${VIRTUALITY_SKIP_REQUIREMENTS:-0}"

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
  echo -e "${CYAN}${BOLD}│${RESET} ${BOLD}Virtuality Node Installer${RESET}                              ${CYAN}${BOLD}│${RESET}"
  echo -e "${CYAN}${BOLD}│${RESET} KVM / QEMU / libvirt / Cockpit virtualization node       ${CYAN}${BOLD}│${RESET}"
  echo -e "${CYAN}${BOLD}╰────────────────────────────────────────────────────────────╯${RESET}"
  echo
  echo -e "${GRAY}Лог установки:${RESET} ${LOG_FILE}"
  echo -e "${GRAY}Storage:${RESET} ${STORAGE_DIR}"
  echo -e "${GRAY}Cockpit:${RESET} ${COCKPIT_PORT}/tcp"
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
fail() { echo -e "  ${RED}✗${RESET} $*"; log "ERROR: $*"; echo; echo -e "${RED}${BOLD}Установка остановлена.${RESET} Лог: ${LOG_FILE}"; exit 1; }

run_logged() {
  local description="$1"
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
    printf "\r  ${CYAN}%s${RESET} %s... ${GRAY}%ss${RESET} ${GRAY}(лог: %s)${RESET}" "$symbol" "$description" "$elapsed" "$LOG_FILE"
    sleep 1
    i=$((i + 1))
  done
  wait "$pid"
  code=$?
  printf "\r%*s\r" 120 ""
  if [[ "$code" -eq 0 ]]; then
    ok "$description"
  else
    fail "$description"
  fi
}

service_state() { systemctl is-active "$1" 2>/dev/null || echo "inactive"; }
service_enabled() { systemctl is-enabled "$1" 2>/dev/null || echo "disabled"; }
free_mb_for_path() { local path="$1"; mkdir -p "$path" 2>/dev/null || true; df -Pm "$path" | awk 'NR==2 {print $4}'; }

check_requirements() {
  step "Проверяем системные требования"
  if [[ "$SKIP_REQUIREMENTS" == "1" ]]; then
    warn "Проверка требований отключена через VIRTUALITY_SKIP_REQUIREMENTS=1"
    return 0
  fi

  local root_free var_free ram_mb cpu_cores
  root_free="$(free_mb_for_path /)"
  var_free="$(free_mb_for_path /var/lib 2>/dev/null || free_mb_for_path /)"
  ram_mb="$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)"
  cpu_cores="$(nproc 2>/dev/null || echo 1)"

  (( root_free >= MIN_ROOT_FREE_MB )) && ok "Свободно на /: ${root_free} MB минимум ${MIN_ROOT_FREE_MB} MB" || fail "Недостаточно места на /: ${root_free} MB. Нужно минимум ${MIN_ROOT_FREE_MB} MB"
  (( var_free >= MIN_VAR_FREE_MB )) && ok "Свободно для /var/lib: ${var_free} MB минимум ${MIN_VAR_FREE_MB} MB" || fail "Недостаточно места для VM-хранилища /var/lib: ${var_free} MB. Нужно минимум ${MIN_VAR_FREE_MB} MB"
  (( ram_mb >= MIN_RAM_MB )) && ok "RAM: ${ram_mb} MB минимум ${MIN_RAM_MB} MB" || warn "RAM: ${ram_mb} MB меньше рекомендуемых ${MIN_RAM_MB} MB"
  (( cpu_cores >= MIN_CPU_CORES )) && ok "CPU cores: ${cpu_cores} минимум ${MIN_CPU_CORES}" || warn "CPU cores: ${cpu_cores} меньше рекомендуемых ${MIN_CPU_CORES}"
}

print_header

step "Проверяем права и совместимость ОС"
if [[ "$EUID" -ne 0 ]]; then
  fail "Запусти от root: sudo bash install_virtuality_node.sh"
fi
ok "Права root подтверждены"

if ! command -v apt >/dev/null 2>&1; then
  fail "Установщик рассчитан на Debian/Ubuntu с apt"
fi
ok "apt найден"

if [[ -f /etc/os-release ]]; then
  OS_NAME="$(. /etc/os-release && echo "${PRETTY_NAME}")"
  ok "Система: ${OS_NAME}"
else
  warn "Не найден /etc/os-release"
fi

check_requirements

step "Проверяем аппаратную виртуализацию"
if grep -E -q '(vmx|svm)' /proc/cpuinfo; then
  ok "CPU поддерживает аппаратную виртуализацию vmx/svm"
else
  warn "vmx/svm не найдено. Проверь виртуализацию в BIOS/UEFI"
fi
if [[ -e /dev/kvm ]]; then
  ok "/dev/kvm уже доступен"
else
  warn "/dev/kvm пока не найден. После установки модулей проверь через sudo vhealth"
fi

step "Обновляем apt cache"
run_logged "apt update выполнен" apt update

step "Устанавливаем базовые утилиты"
run_logged "Базовые пакеты установлены" apt install -y \
  curl wget git nano htop unzip ca-certificates gnupg lsb-release \
  software-properties-common apt-transport-https ufw rsync

step "Устанавливаем KVM/QEMU/libvirt"
run_logged "Пакеты виртуализации установлены" apt install -y \
  qemu-kvm qemu-utils libvirt-daemon-system libvirt-clients virtinst \
  bridge-utils dnsmasq-base ovmf swtpm cloud-image-utils

step "Устанавливаем Cockpit и модули"
run_logged "Cockpit установлен" apt install -y \
  cockpit cockpit-machines cockpit-networkmanager cockpit-storaged cockpit-packagekit

step "Создаём структуру Virtuality"
run_logged "Директории Virtuality созданы" mkdir -p "$PROJECT_DIR" "$STORAGE_DIR" "$ISO_DIR" "$IMAGES_DIR" "$BACKUP_DIR" "$LOG_DIR"
run_logged "Права директорий выставлены" chmod 755 "$PROJECT_DIR" "$STORAGE_DIR" "$ISO_DIR" "$IMAGES_DIR" "$BACKUP_DIR" "$LOG_DIR"
ok "Project: ${PROJECT_DIR}"
ok "ISO: ${ISO_DIR}"
ok "Images: ${IMAGES_DIR}"
ok "Backups: ${BACKUP_DIR}"

step "Добавляем пользователя в группы libvirt/kvm"
REAL_USER="${SUDO_USER:-root}"
if [[ "$REAL_USER" != "root" ]]; then
  usermod -aG libvirt "$REAL_USER" >> "$LOG_FILE" 2>&1 || warn "Не удалось добавить ${REAL_USER} в libvirt"
  usermod -aG kvm "$REAL_USER" >> "$LOG_FILE" 2>&1 || warn "Не удалось добавить ${REAL_USER} в kvm"
  ok "Пользователь ${REAL_USER} добавлен в группы libvirt/kvm"
  warn "Чтобы группы применились, нужно выйти из SSH и зайти снова"
else
  warn "Запуск под root без SUDO_USER; пользователь не добавлен в группы"
fi

step "Включаем systemd-сервисы"
run_logged "libvirtd включён и запущен" systemctl enable --now libvirtd
run_logged "virtlogd включён и запущен" systemctl enable --now virtlogd
run_logged "cockpit.socket включён и запущен" systemctl enable --now cockpit.socket
ok "libvirtd: $(service_state libvirtd) / $(service_enabled libvirtd)"
ok "virtlogd: $(service_state virtlogd) / $(service_enabled virtlogd)"
ok "cockpit.socket: $(service_state cockpit.socket) / $(service_enabled cockpit.socket)"

step "Настраиваем firewall"
run_logged "Разрешён OpenSSH" ufw allow OpenSSH
run_logged "Разрешён Cockpit ${COCKPIT_PORT}/tcp" ufw allow "${COCKPIT_PORT}/tcp"
run_logged "Разрешены VNC-порты 5900:5999/tcp" ufw allow 5900:5999/tcp
if ufw status | grep -qi inactive; then
  echo "y" | ufw enable >> "$LOG_FILE" 2>&1 && ok "UFW включён" || warn "Не удалось включить UFW"
else
  ok "UFW уже активен"
fi

step "Создаём libvirt storage pools"
virsh pool-define-as virtuality-images dir --target "$IMAGES_DIR" >> "$LOG_FILE" 2>&1 || true
virsh pool-start virtuality-images >> "$LOG_FILE" 2>&1 || true
virsh pool-autostart virtuality-images >> "$LOG_FILE" 2>&1 || true
ok "Storage pool virtuality-images готов"

virsh pool-define-as virtuality-iso dir --target "$ISO_DIR" >> "$LOG_FILE" 2>&1 || true
virsh pool-start virtuality-iso >> "$LOG_FILE" 2>&1 || true
virsh pool-autostart virtuality-iso >> "$LOG_FILE" 2>&1 || true
ok "Storage pool virtuality-iso готов"

step "Создаём конфигурацию окружения"
cat > "${PROJECT_DIR}/virtuality.env" <<EOF
PROJECT_NAME="${PROJECT_NAME}"
PROJECT_DIR="${PROJECT_DIR}"
STORAGE_DIR="${STORAGE_DIR}"
ISO_DIR="${ISO_DIR}"
IMAGES_DIR="${IMAGES_DIR}"
BACKUP_DIR="${BACKUP_DIR}"
LOG_DIR="${LOG_DIR}"
COCKPIT_PORT="${COCKPIT_PORT}"
EOF
ok "Создан ${PROJECT_DIR}/virtuality.env"

step "Финальная диагностика"
if command -v virsh >/dev/null 2>&1; then
  virsh list --all >> "$LOG_FILE" 2>&1 && ok "virsh list работает" || warn "virsh list вернул предупреждение"
  virsh pool-list --all >> "$LOG_FILE" 2>&1 && ok "virsh pool-list работает" || warn "virsh pool-list вернул предупреждение"
else
  warn "virsh не найден после установки"
fi
if ss -tulpn 2>/dev/null | grep -q ':9090'; then
  ok "Cockpit слушает порт 9090"
else
  warn "Порт 9090 не виден в ss; Cockpit может быть socket-activated"
fi
if [[ -e /dev/kvm ]]; then
  ok "/dev/kvm доступен"
else
  warn "/dev/kvm не найден после установки"
fi

SERVER_IP="$(hostname -I | awk '{print $1}')"

echo
echo -e "${GREEN}${BOLD}╭────────────────────────────────────────────────────────────╮${RESET}"
echo -e "${GREEN}${BOLD}│${RESET} ${BOLD}Установка Virtuality Node завершена${RESET}                      ${GREEN}${BOLD}│${RESET}"
echo -e "${GREEN}${BOLD}╰────────────────────────────────────────────────────────────╯${RESET}"
echo
echo -e "${BOLD}Cockpit:${RESET}     https://${SERVER_IP}:${COCKPIT_PORT}"
echo -e "${BOLD}Project:${RESET}     ${PROJECT_DIR}"
echo -e "${BOLD}ISO:${RESET}         ${ISO_DIR}"
echo -e "${BOLD}Images:${RESET}      ${IMAGES_DIR}"
echo -e "${BOLD}Backups:${RESET}     ${BACKUP_DIR}"
echo -e "${BOLD}Install log:${RESET} ${LOG_FILE}"
echo
echo -e "${BOLD}Проверка:${RESET}"
echo "  virsh list --all"
echo "  virsh pool-list --all"
echo "  systemctl status libvirtd --no-pager"
echo "  systemctl status cockpit.socket --no-pager"
echo
line
echo -e "${DIM}Следующий шаг: sudo bash scripts/setup_bridge_br0.sh enp2s0 static${RESET}"
echo -e "${DIM}После bridge: sudo bash scripts/create_test_vm.sh${RESET}"
line
