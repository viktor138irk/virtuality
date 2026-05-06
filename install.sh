#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Virtuality public installer entrypoint
# Preferred one-command install file.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/viktor138irk/virtuality/main/install.sh | sudo bash
# ==========================================================

INSTALL_URL="${VIRTUALITY_BOOTSTRAP_URL:-https://raw.githubusercontent.com/viktor138irk/virtuality/main/bootstrap.sh}"

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$INSTALL_URL" | bash
elif command -v wget >/dev/null 2>&1; then
  wget -qO- "$INSTALL_URL" | bash
else
  echo "Ошибка: нужен curl или wget для загрузки установщика Virtuality."
  exit 1
fi
