#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${REPO_DIR}/scripts/virtuality_healthcheck.sh"
DST="/usr/local/bin/vhealth"
TARGET_USER="${SUDO_USER:-${USER:-root}}"

if [[ "$EUID" -ne 0 ]]; then
  echo "Ошибка: запусти через sudo: sudo bash scripts/install_healthcheck_command.sh"
  exit 1
fi

if [[ ! -f "$SRC" ]]; then
  echo "Ошибка: не найден $SRC"
  exit 1
fi

install -m 0755 "$SRC" "$DST"

USER_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6 || true)"
if [[ -n "$USER_HOME" && -d "$USER_HOME" ]]; then
  BASHRC="$USER_HOME/.bashrc"
  touch "$BASHRC"
  grep -q "alias vh=" "$BASHRC" || cat >> "$BASHRC" <<'EOF'

# Virtuality health aliases
alias vh='sudo vhealth'
alias vhealth-full='sudo vhealth'
EOF
  chown "$TARGET_USER:$TARGET_USER" "$BASHRC" || true
fi

echo "============================================================"
echo "Virtuality healthcheck installed"
echo "============================================================"
echo "Command: sudo vhealth"
echo "Alias after source ~/.bashrc: vh"
echo "============================================================"
