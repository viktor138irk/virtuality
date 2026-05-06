#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Virtuality public installer entrypoint
# Universal one-command install file.
#
# Works both as root and as a regular sudo-capable user:
#   curl -fL https://raw.githubusercontent.com/viktor138irk/virtuality/main/install.sh | bash
# ==========================================================

INSTALL_URL="${VIRTUALITY_BOOTSTRAP_URL:-https://raw.githubusercontent.com/viktor138irk/virtuality/main/bootstrap.sh}"
TMP_FILE="/tmp/virtuality-install-$$.sh"

ESC="\033"
RESET="${ESC}[0m"
BOLD="${ESC}[1m"
GREEN="${ESC}[32m"
RED="${ESC}[31m"
CYAN="${ESC}[36m"
GRAY="${ESC}[90m"

cleanup() {
  rm -f "$TMP_FILE" 2>/dev/null || true
}
trap cleanup EXIT

ok() { echo -e "${GREEN}✓${RESET} $*"; }
fail() { echo -e "${RED}✗${RESET} $*"; exit 1; }

cat <<EOF
${CYAN}${BOLD}Virtuality installer${RESET}
${GRAY}Bootstrap:${RESET} ${INSTALL_URL}
EOF

echo

echo "[1/3] Загружаем основной установщик..."
if command -v curl >/dev/null 2>&1; then
  curl -fL --show-error "$INSTALL_URL" -o "$TMP_FILE" || fail "Не удалось скачать bootstrap. Проверь, что репозиторий публичный и URL доступен."
elif command -v wget >/dev/null 2>&1; then
  wget -O "$TMP_FILE" "$INSTALL_URL" || fail "Не удалось скачать bootstrap. Проверь, что репозиторий публичный и URL доступен."
else
  fail "Нужен curl или wget для загрузки установщика Virtuality."
fi

if [[ ! -s "$TMP_FILE" ]]; then
  fail "Скачанный bootstrap пустой. Чаще всего это private repository или проблема сети."
fi

chmod +x "$TMP_FILE"
ok "Установщик скачан: $TMP_FILE"

echo
echo "[2/3] Проверяем режим запуска..."
if [[ "${EUID}" -eq 0 ]]; then
  ok "Запуск под root"
  echo
  echo "[3/3] Запускаем установку Virtuality..."
  bash "$TMP_FILE" || fail "Установка остановлена с ошибкой. Выше должна быть причина. Логи: /var/log/virtuality/"
else
  if ! command -v sudo >/dev/null 2>&1; then
    fail "Нужен root или sudo. Запусти под root или установи sudo."
  fi

  ok "Запуск под обычным пользователем, будет использован sudo"
  echo
  echo "[3/3] Запускаем установку Virtuality через sudo..."

  sudo --preserve-env=VIRTUALITY_USER,VIRTUALITY_WEB_PORT,VIRTUALITY_MIN_ROOT_FREE_MB,VIRTUALITY_MIN_VAR_FREE_MB,VIRTUALITY_MIN_RAM_MB,VIRTUALITY_MIN_CPU_CORES,VIRTUALITY_SKIP_REQUIREMENTS,VIRTUALITY_SETUP_BRIDGE,VIRTUALITY_BRIDGE_IFACE,VIRTUALITY_CREATE_TEST_VM,VIRTUALITY_REPO_URL,VIRTUALITY_PROJECT_BASE_DIR,VIRTUALITY_PROJECT_DIR,VIRTUALITY_BOOTSTRAP_URL bash "$TMP_FILE" || fail "Установка остановлена с ошибкой. Выше должна быть причина. Логи: /var/log/virtuality/"
fi

ok "Установка завершена"
