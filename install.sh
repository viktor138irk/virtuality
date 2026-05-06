#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Virtuality public installer entrypoint
# Universal one-command install file.
#
# Works both as root and as a regular sudo-capable user:
#   curl -fsSL https://raw.githubusercontent.com/viktor138irk/virtuality/main/install.sh | bash
# ==========================================================

INSTALL_URL="${VIRTUALITY_BOOTSTRAP_URL:-https://raw.githubusercontent.com/viktor138irk/virtuality/main/bootstrap.sh}"
TMP_FILE="/tmp/virtuality-install-$$.sh"

cleanup() {
  rm -f "$TMP_FILE" 2>/dev/null || true
}
trap cleanup EXIT

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$INSTALL_URL" -o "$TMP_FILE"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$TMP_FILE" "$INSTALL_URL"
else
  echo "Ошибка: нужен curl или wget для загрузки установщика Virtuality."
  exit 1
fi

chmod +x "$TMP_FILE"

if [[ "${EUID}" -eq 0 ]]; then
  bash "$TMP_FILE"
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Ошибка: нужен root или sudo. Запусти под root или установи sudo."
    exit 1
  fi
  sudo --preserve-env=VIRTUALITY_USER,VIRTUALITY_WEB_PORT,VIRTUALITY_MIN_ROOT_FREE_MB,VIRTUALITY_MIN_VAR_FREE_MB,VIRTUALITY_MIN_RAM_MB,VIRTUALITY_MIN_CPU_CORES,VIRTUALITY_SKIP_REQUIREMENTS,VIRTUALITY_SETUP_BRIDGE,VIRTUALITY_BRIDGE_IFACE,VIRTUALITY_CREATE_TEST_VM,VIRTUALITY_REPO_URL,VIRTUALITY_PROJECT_BASE_DIR,VIRTUALITY_PROJECT_DIR,VIRTUALITY_BOOTSTRAP_URL bash "$TMP_FILE"
fi
