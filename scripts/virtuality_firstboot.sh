#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Virtuality first boot
# Runs once on a system installed from the Virtuality image.
# The web panel is installed first (offline, from bundled
# wheels) so the setup wizard in the browser can show the
# progress of everything that follows: network, KVM/libvirt,
# tools. Progress is written to config/setup.json, which the
# wizard polls. Every step is idempotent: on failure systemd
# restarts the script after a minute and it continues from
# the first unfinished step.
# ==========================================================

SOURCE_DIR="${VIRTUALITY_SOURCE_DIR:-/opt/virtuality/source}"
IMAGE_ENV="${VIRTUALITY_IMAGE_ENV:-/etc/virtuality/image.env}"
STATE_DIR="${VIRTUALITY_STATE_DIR:-/var/lib/virtuality}"
CONFIG_DIR="${STATE_DIR}/config"
STATE_FILE="${CONFIG_DIR}/setup.json"
DONE_FLAG="${STATE_DIR}/.firstboot-done"
STEP_FLAG_DIR="${STATE_DIR}/.firstboot-steps"
LOG_DIR="${VIRTUALITY_LOG_DIR:-/var/log/virtuality}"
LOG_FILE="${LOG_DIR}/firstboot.log"
APT_LOCK_CONF="${VIRTUALITY_APT_LOCK_CONF:-/etc/apt/apt.conf.d/90virtuality-lock-timeout}"
ISSUE_FILE="${VIRTUALITY_ISSUE_FILE:-/etc/issue.d/virtuality.issue}"
CTL="${VIRTUALITY_CTL:-/usr/local/bin/virtuality-ctl}"
NETWORK_WAIT_SECONDS="${VIRTUALITY_NETWORK_WAIT_SECONDS:-0}"   # 0 = wait forever

STEP_IDS=(panel network virtualization tools finish)
declare -A STEP_TITLE=(
  [panel]="Панель управления"
  [network]="Подключение к интернету"
  [virtualization]="KVM, QEMU и libvirt"
  [tools]="Инструменты диагностики"
  [finish]="Завершение"
)
declare -A STEP_STATUS=()
CURRENT_STEP=""
WATCHER_PID=""

mkdir -p "$LOG_DIR" "$STATE_DIR" "$CONFIG_DIR" "$STEP_FLAG_DIR" "$(dirname "$ISSUE_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

now() { date '+%Y-%m-%d %H:%M:%S'; }
say() { echo "[$(now)] [virtuality-firstboot] $*"; }

# ---------------------------------------------------------------- progress for the wizard
json_escape() {
  local text="$1"
  text="${text//\\/\\\\}"
  text="${text//\"/\\\"}"
  text="${text//$'\n'/ }"
  printf '%s' "$text"
}

write_state() {
  local stage="$1" message="$2" id first=1
  {
    printf '{"stage": "%s", "message": "%s", "updated_at": "%s", "steps": [' "$stage" "$(json_escape "$message")" "$(now)"
    for id in "${STEP_IDS[@]}"; do
      (( first )) || printf ', '
      first=0
      printf '{"id": "%s", "title": "%s", "status": "%s"}' "$id" "${STEP_TITLE[$id]}" "${STEP_STATUS[$id]:-pending}"
    done
    printf ']}\n'
  } > "${STATE_FILE}.tmp"
  mv -f "${STATE_FILE}.tmp" "$STATE_FILE"
  chmod 644 "$STATE_FILE"
}

step_done_before() { [[ -f "${STEP_FLAG_DIR}/$1" ]]; }

step_start() {
  CURRENT_STEP="$1"
  STEP_STATUS[$1]="running"
  say "step ${1}: ${STEP_TITLE[$1]}"
  write_state installing "$2"
}

step_finish() {
  STEP_STATUS[$1]="done"
  touch "${STEP_FLAG_DIR}/$1"
  CURRENT_STEP=""
  write_state installing "${2:-}"
}

# apt writes its progress to the installer logs; turn the last line into a friendly message.
watch_progress() {
  local latest line pkg
  while true; do
    latest="$(ls -t "${LOG_DIR}"/install_*.log 2>/dev/null | head -n1 || true)"
    if [[ -n "$latest" ]]; then
      line="$(grep -aE '^(Get:[0-9]+ |Unpacking |Setting up |Preparing to unpack )' "$latest" 2>/dev/null | tail -n1 || true)"
      pkg=""
      case "$line" in
        Get:*) pkg="$(printf '%s' "$line" | awk '{print $5}')" ; [[ -n "$pkg" ]] && write_state installing "Скачиваем ${pkg}…" ;;
        "Unpacking "*|"Preparing to unpack "*) pkg="$(printf '%s' "$line" | sed -E 's/^(Preparing to unpack \.\.\.\/|Preparing to unpack |Unpacking )//; s/[ :].*//')"; [[ -n "$pkg" ]] && write_state installing "Распаковываем ${pkg}…" ;;
        "Setting up "*) pkg="$(printf '%s' "$line" | sed -E 's/^Setting up //; s/[ :].*//')"; [[ -n "$pkg" ]] && write_state installing "Настраиваем ${pkg}…" ;;
      esac
    fi
    sleep 3
  done
}

watch_start() { watch_progress & WATCHER_PID=$!; }
watch_stop() { [[ -n "$WATCHER_PID" ]] && { kill "$WATCHER_PID" 2>/dev/null || true; wait "$WATCHER_PID" 2>/dev/null || true; }; WATCHER_PID=""; }

on_error() {
  local code=$?
  watch_stop
  if [[ -n "$CURRENT_STEP" ]]; then
    STEP_STATUS[$CURRENT_STEP]="error"
  fi
  write_state error "Шаг «${STEP_TITLE[${CURRENT_STEP:-finish}]}» завершился с ошибкой (код ${code}). Повторим автоматически через минуту; подробности ниже в журнале."
  say "ERROR: step ${CURRENT_STEP:-?} failed with code ${code}"
  exit "$code"
}
trap on_error ERR

# ---------------------------------------------------------------- environment
if [[ -f "$DONE_FLAG" ]]; then
  say "already completed, nothing to do"
  exit 0
fi

if [[ -f "$IMAGE_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$IMAGE_ENV"
  set +a
fi

first_human_user() {
  getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 && $7 !~ /(nologin|false)$/ {print $1; exit}'
}

AUTH_USER="${VIRTUALITY_USER:-$(first_human_user)}"
AUTH_USER="${AUTH_USER:-root}"
export VIRTUALITY_USER="$AUTH_USER"
export VIRTUALITY_AUTH_USER="$AUTH_USER"
export VIRTUALITY_WEB_PORT="${VIRTUALITY_WEB_PORT:-8088}"
export VIRTUALITY_AUTO_UPDATE="${VIRTUALITY_AUTO_UPDATE:-1}"
# Disk/RAM limits are reported in the panel; they must not block an image install.
export VIRTUALITY_SKIP_REQUIREMENTS="${VIRTUALITY_SKIP_REQUIREMENTS:-1}"
export VIRTUALITY_FIRSTBOOT=1
export DEBIAN_FRONTEND=noninteractive
export TERM="${TERM:-dumb}"

for id in "${STEP_IDS[@]}"; do
  step_done_before "$id" && STEP_STATUS[$id]="done"
done

say "source: ${SOURCE_DIR}, panel user: ${AUTH_USER}, port: ${VIRTUALITY_WEB_PORT}"
[[ -f "${SOURCE_DIR}/install_virtuality_node.sh" ]] || { write_state error "Исходники Virtuality не найдены в ${SOURCE_DIR}"; say "ERROR: source not found"; exit 1; }
cd "$SOURCE_DIR"

# unattended-upgrades often holds the dpkg lock right after the first boot.
printf 'DPkg::Lock::Timeout "900";\nDPkg::Options { "--force-confdef"; "--force-confold"; };\n' > "$APT_LOCK_CONF"

cat > "$ISSUE_FILE" <<'EOF'
Virtuality настраивается. Ход установки: sudo journalctl -fu virtuality-firstboot

EOF

network_ready() {
  [[ -n "${VIRTUALITY_NETWORK_CHECK:-}" ]] && { "$VIRTUALITY_NETWORK_CHECK"; return; }
  ip route show default 2>/dev/null | grep -q . && getent hosts archive.ubuntu.com >/dev/null 2>&1
}

wait_for_network() {
  local started elapsed
  started="$(date +%s)"
  until network_ready; do
    elapsed=$(( $(date +%s) - started ))
    if (( NETWORK_WAIT_SECONDS > 0 && elapsed >= NETWORK_WAIT_SECONDS )); then
      say "ERROR: no network with DNS after ${elapsed}s"
      return 1
    fi
    if (( elapsed >= 20 )); then
      write_state waiting-network "Нет доступа в интернет. Подключите кабель или настройте сеть — установка продолжится сама."
    fi
    sleep 5
  done
  write_state installing "Интернет доступен"
}

# ---------------------------------------------------------------- 1. panel (offline)
if ! step_done_before panel; then
  step_start panel "Устанавливаем панель управления…"
  if ! VIRTUALITY_PANEL_ONLY=1 bash ./scripts/install_web_panel.sh; then
    # No bundled wheels (image built with --no-wheels): install online instead.
    say "offline panel install failed, retrying with network"
    wait_for_network
    bash ./scripts/install_web_panel.sh
  fi
  step_finish panel "Панель управления запущена"
fi

# ---------------------------------------------------------------- 2. network
if ! step_done_before network; then
  step_start network "Проверяем подключение к интернету…"
  wait_for_network
  step_finish network "Интернет доступен"
fi

# ---------------------------------------------------------------- 3. virtualization
if ! step_done_before virtualization; then
  step_start virtualization "Устанавливаем пакеты виртуализации (это самый долгий шаг)…"
  watch_start
  bash ./install_virtuality_node.sh
  watch_stop
  step_finish virtualization "Виртуализация установлена"
fi

# ---------------------------------------------------------------- 4. tools
if ! step_done_before tools; then
  step_start tools "Устанавливаем инструменты диагностики…"
  bash ./scripts/install_healthcheck_command.sh
  step_finish tools "Инструменты установлены"
fi

# ---------------------------------------------------------------- 5. finish
step_start finish "Завершаем настройку…"
# The panel was installed before libvirt: refresh units/firewall and restart it with everything in place.
"$CTL" reconfigure
PANEL_URL="$("$CTL" url 2>/dev/null || echo "http://$(hostname -I | awk '{print $1}'):${VIRTUALITY_WEB_PORT}")"
cat > "$ISSUE_FILE" <<EOF
Virtuality: откройте панель управления в браузере — ${PANEL_URL}
Логин: ${AUTH_USER} (пароль как у пользователя Linux)

EOF
touch "$DONE_FLAG"
STEP_STATUS[finish]="done"
CURRENT_STEP=""
write_state done "Сервер готов"
systemctl disable virtuality-firstboot.service >/dev/null 2>&1 || true
rm -f "$APT_LOCK_CONF"
say "completed. Panel: ${PANEL_URL} login: ${AUTH_USER}"
