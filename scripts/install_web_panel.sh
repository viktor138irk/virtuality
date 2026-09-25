#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Virtuality web panel installer
# Copies web/ to /opt/virtuality/web (keeping the previous
# version for rollback), builds the virtualenv (offline from
# bundled wheels when available), installs virtuality-ctl and
# lets it create the systemd units, TLS and firewall rules.
#
#   VIRTUALITY_PANEL_ONLY=1   skip apt entirely (first boot from the image,
#                             before the network is up)
#   VIRTUALITY_AUTH_USER      Linux user that logs in to the panel
#   VIRTUALITY_WEB_PORT       HTTP port (default 8088)
# ==========================================================

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="${REPO_DIR}/web"
APP_DIR="/opt/virtuality/web"
VENV_DIR="/opt/virtuality/venv"
CTL_SRC="${REPO_DIR}/scripts/virtuality-ctl"
CTL_DST="/usr/local/bin/virtuality-ctl"
LOG_DIR="/var/log/virtuality"
LOG_FILE="${LOG_DIR}/install_web_panel_$(date +%Y%m%d_%H%M%S).log"
CONFIG_DIR="/var/lib/virtuality/config"
PROFILE_FILE="${CONFIG_DIR}/host_profile.json"
SESSION_SECRET_FILE="${CONFIG_DIR}/session_secret"
NODE_CONFIG_FILE="${CONFIG_DIR}/web.env"
SETUP_STATE_FILE="${CONFIG_DIR}/setup.json"
UPLOAD_TMP_DIR="/var/lib/virtuality/tmp"
WHEELS_DIR="${VIRTUALITY_WHEELS_DIR:-${REPO_DIR}/wheels}"
PANEL_ONLY="${VIRTUALITY_PANEL_ONLY:-0}"
export DEBIAN_FRONTEND=noninteractive

# Settings survive updates: explicit env > saved node config > defaults.
saved_setting() {
  local key="$1"
  [[ -f "$NODE_CONFIG_FILE" ]] || return 0
  sed -n "s/^${key}=//p" "$NODE_CONFIG_FILE" | tail -n1 | tr -d "\"'"
}
first_human_user() {
  getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 && $7 !~ /(nologin|false)$/ {print $1; exit}'
}
env_set() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$NODE_CONFIG_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$NODE_CONFIG_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$NODE_CONFIG_FILE"
  fi
}

PORT="${VIRTUALITY_WEB_PORT:-$(saved_setting VIRTUALITY_WEB_PORT)}"
PORT="${PORT:-8088}"
AUTH_USER="${VIRTUALITY_AUTH_USER:-$(saved_setting VIRTUALITY_AUTH_USER)}"
AUTH_USER="${AUTH_USER:-${SUDO_USER:-}}"
AUTH_USER="${AUTH_USER:-$(first_human_user)}"
AUTH_USER="${AUTH_USER:-root}"
AUTO_UPDATE="${VIRTUALITY_AUTO_UPDATE:-$(saved_setting VIRTUALITY_AUTO_UPDATE)}"
AUTO_UPDATE="${AUTO_UPDATE:-1}"
CURRENT_STEP=0
TOTAL_STEPS=9

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

mkdir -p "$LOG_DIR" "$CONFIG_DIR" "$UPLOAD_TMP_DIR"
chmod 1777 "$UPLOAD_TMP_DIR"

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }
line() { printf '%*s\n' 72 '' | tr ' ' '─'; }
log() { echo "[$(timestamp)] $*" >> "$LOG_FILE"; }

step() {
  CURRENT_STEP=$((CURRENT_STEP + 1))
  echo
  echo -e "${BLUE}${BOLD}[${CURRENT_STEP}/${TOTAL_STEPS}]${RESET} ${BOLD}$*${RESET}"
  log "STEP ${CURRENT_STEP}/${TOTAL_STEPS}: $*"
}
ok() { echo -e "  ${GREEN}✓${RESET} $*"; log "OK: $*"; }
warn() { echo -e "  ${YELLOW}!${RESET} $*"; log "WARN: $*"; }
fail() { echo -e "  ${RED}✗${RESET} $*"; log "ERROR: $*"; echo; echo -e "${RED}${BOLD}Установка остановлена.${RESET} Подробности в логе: ${LOG_FILE}"; tail -n 20 "$LOG_FILE" 2>/dev/null || true; exit 1; }

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

json_value() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "$PROFILE_FILE" "$1" 2>/dev/null || true; }

echo -e "${CYAN}${BOLD}Virtuality — установка панели управления${RESET}"
echo -e "${GRAY}Лог: ${LOG_FILE}${RESET}"

step "Проверяем права и исходники"
[[ "$EUID" -eq 0 ]] || fail "Запустите через sudo: sudo bash scripts/install_web_panel.sh"
[[ -f "$WEB_DIR/app.py" && -f "$WEB_DIR/requirements.txt" ]] || fail "Не найдены исходники панели в ${WEB_DIR}"
[[ -f "$CTL_SRC" ]] || fail "Не найден ${CTL_SRC}"
for cmd in systemctl rsync python3 hostname openssl; do
  command -v "$cmd" >/dev/null 2>&1 || fail "Не найдена команда ${cmd}"
done
ok "Исходники: ${WEB_DIR}"
if id "$AUTH_USER" >/dev/null 2>&1; then
  ok "Пользователь для входа: ${AUTH_USER}"
else
  fail "Пользователь ${AUTH_USER} не найден. Создайте его или передайте VIRTUALITY_AUTH_USER=имя"
fi
if getent shadow "$AUTH_USER" | cut -d: -f2 | grep -Eq '^(!|\*|!!)?$'; then
  warn "У пользователя ${AUTH_USER} нет пароля — задайте его: sudo passwd ${AUTH_USER}"
fi

step "Определяем профиль сервера"
if [[ -x "${REPO_DIR}/scripts/detect_host_profile.sh" ]]; then
  PROFILE="$("${REPO_DIR}/scripts/detect_host_profile.sh" 2>>"$LOG_FILE" | tail -n1 || true)"
else
  bash "${REPO_DIR}/scripts/detect_host_profile.sh" >>"$LOG_FILE" 2>&1 || true
  PROFILE="$(json_value profile)"
fi
ok "Профиль: $(json_value label)${PROFILE:+ (${PROFILE})}"

step "Системные пакеты"
if [[ "$PANEL_ONLY" == "1" ]]; then
  ok "Пропущено (VIRTUALITY_PANEL_ONLY=1): пакеты поставит установщик ноды"
elif command -v apt-get >/dev/null 2>&1; then
  run_logged "apt update выполнен" apt-get update
  run_logged "Пакеты панели установлены" apt-get install -y python3 python3-venv rsync openssl curl nftables novnc python3-websockify qemu-utils virtinst libvirt-clients cloud-image-utils
else
  warn "apt не найден — пакеты не устанавливались"
fi

step "Копируем панель в ${APP_DIR}"
run_logged "Новая версия скопирована" rsync -a --delete --exclude='__pycache__/' --exclude='.env' "$WEB_DIR/" "${APP_DIR}.new/"
install -m 0644 "${REPO_DIR}/VERSION" "${APP_DIR}.new/VERSION"
FRESH_INSTALL=1
if [[ -d "$APP_DIR" ]]; then
  FRESH_INSTALL=0
  rm -rf "${APP_DIR}.prev"
  mv "$APP_DIR" "${APP_DIR}.prev"
  ok "Предыдущая версия сохранена в ${APP_DIR}.prev (virtuality-ctl rollback)"
fi
mv "${APP_DIR}.new" "$APP_DIR"
ok "Версия панели: $(cat "${APP_DIR}/VERSION")"

step "Python-окружение"
if [[ -x "$VENV_DIR/bin/python" ]] && "$VENV_DIR/bin/python" -c 'import sys' >/dev/null 2>&1; then
  ok "Virtualenv уже есть: ${VENV_DIR}"
else
  [[ -d "$VENV_DIR" ]] && { warn "Virtualenv повреждён (например, после обновления Python) — пересоздаём"; rm -rf -- "${VENV_DIR:?}"; }
  if python3 -c 'import ensurepip' >/dev/null 2>&1; then
    run_logged "Virtualenv создан" python3 -m venv "$VENV_DIR"
  else
    # Ubuntu Server without python3-venv: pip is bootstrapped from the bundled wheel.
    run_logged "Virtualenv создан (без pip)" python3 -m venv --without-pip "$VENV_DIR"
  fi
fi
if ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
  PIP_WHEEL="$(ls "${WHEELS_DIR}"/pip-*.whl 2>/dev/null | head -n1 || true)"
  [[ -n "$PIP_WHEEL" ]] || fail "В virtualenv нет pip и нет wheel pip в ${WHEELS_DIR}. Установите python3-venv: sudo apt install python3-venv"
  run_logged "pip установлен из ${PIP_WHEEL##*/}" "$VENV_DIR/bin/python" "${PIP_WHEEL}/pip" install --no-index --find-links "$WHEELS_DIR" pip
fi
if compgen -G "${WHEELS_DIR}/*.whl" >/dev/null; then
  run_logged "Зависимости установлены офлайн из ${WHEELS_DIR}" "$VENV_DIR/bin/python" -m pip install --no-index --find-links "$WHEELS_DIR" -r "$APP_DIR/requirements.txt"
else
  run_logged "Зависимости установлены из PyPI" "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"
fi
run_logged "Панель импортируется без ошибок" bash -c "cd '$APP_DIR' && '$VENV_DIR/bin/python' -c 'import app'"

step "Настройки ноды"
touch "$NODE_CONFIG_FILE"
grep -q '^#' "$NODE_CONFIG_FILE" || sed -i '1i # Настройки ноды Virtuality. Менять: sudo virtuality-ctl set KEY=VALUE' "$NODE_CONFIG_FILE"
env_set VIRTUALITY_WEB_PORT "$PORT"
env_set VIRTUALITY_AUTH_USER "$AUTH_USER"
env_set VIRTUALITY_AUTO_UPDATE "$AUTO_UPDATE"
env_set VIRTUALITY_SOURCE_DIR "$REPO_DIR"
[[ -n "$(saved_setting VIRTUALITY_WEB_HOST)" ]] || env_set VIRTUALITY_WEB_HOST 0.0.0.0
[[ -n "$(saved_setting VIRTUALITY_TLS)" ]] || env_set VIRTUALITY_TLS 0
[[ -n "$(saved_setting VIRTUALITY_UPDATE_CHANNEL)" ]] || env_set VIRTUALITY_UPDATE_CHANNEL stable
chmod 644 "$NODE_CONFIG_FILE"
ok "Сохранено в ${NODE_CONFIG_FILE}"
if [[ -s "$SESSION_SECRET_FILE" ]]; then
  SESSION_SECRET="$(cat "$SESSION_SECRET_FILE")"
else
  SESSION_SECRET="$(openssl rand -hex 32)"
  printf '%s\n' "$SESSION_SECRET" > "$SESSION_SECRET_FILE"
  chmod 600 "$SESSION_SECRET_FILE"
  ok "Создан ключ сессий: ${SESSION_SECRET_FILE}"
fi
cat > "${APP_DIR}/.env" <<EOF
VIRTUALITY_AUTH_USER=${AUTH_USER}
VIRTUALITY_SESSION_SECRET=${SESSION_SECRET}
VIRTUALITY_COOKIE_SECURE=$(saved_setting VIRTUALITY_COOKIE_SECURE)
TMPDIR=${UPLOAD_TMP_DIR}
TEMP=${UPLOAD_TMP_DIR}
TMP=${UPLOAD_TMP_DIR}
EOF
chmod 600 "${APP_DIR}/.env"

step "Устанавливаем virtuality-ctl"
install -m 0755 "$CTL_SRC" "$CTL_DST"
ok "${CTL_DST}"

step "Службы, firewall и запуск"
run_logged "Юниты созданы, панель запущена" env VIRTUALITY_SOURCE_DIR="$REPO_DIR" "$CTL_DST" reconfigure
sleep 2
if systemctl is-active --quiet virtuality-web.service; then
  ok "virtuality-web.service работает"
else
  journalctl -u virtuality-web.service -n 40 --no-pager >> "$LOG_FILE" 2>&1 || true
  fail "virtuality-web.service не запустился"
fi

step "Мастер настройки"
if [[ -f "$SETUP_STATE_FILE" ]]; then
  ok "Состояние установки уже ведётся"
elif [[ "${VIRTUALITY_FIRSTBOOT:-0}" == "1" ]]; then
  ok "Прогресс пишет первая загрузка"
elif [[ "$FRESH_INSTALL" != "1" ]]; then
  ok "Обновление уже настроенного сервера — мастер не нужен (его можно запустить в Настройках)"
else
  # A node installed by hand gets the same first-run wizard as one installed from the image.
  printf '{"stage": "done", "message": "", "steps": []}\n' > "$SETUP_STATE_FILE"
  ok "Мастер настройки откроется при первом входе в панель"
fi

PANEL_URL="$("$CTL_DST" url 2>/dev/null || echo "http://$(hostname -I | awk '{print $1}'):${PORT}")"
echo
echo -e "${GREEN}${BOLD}Панель управления установлена${RESET}"
line
echo -e "${BOLD}Адрес:${RESET}      ${PANEL_URL}"
echo -e "${BOLD}Логин:${RESET}      ${AUTH_USER} (пароль пользователя Linux)"
echo -e "${BOLD}Служба:${RESET}     systemctl status virtuality-web --no-pager"
echo -e "${BOLD}Журнал:${RESET}     journalctl -u virtuality-web -f"
echo -e "${BOLD}Настройки:${RESET}  sudo virtuality-ctl status"
line
echo -e "${DIM}Автообновление: $([[ "$AUTO_UPDATE" == "1" ]] && echo "включено (ночью)" || echo "отключено")${RESET}"
