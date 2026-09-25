#!/usr/bin/env bash
set -euo pipefail

# Legacy entrypoint kept for old documentation links.
# All installation logic lives in install.sh.

INSTALL_URL="${VIRTUALITY_INSTALL_URL:-https://raw.githubusercontent.com/viktor138irk/virtuality/main/install.sh}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"

if [[ -n "$SCRIPT_DIR" && -f "${SCRIPT_DIR}/install.sh" ]]; then
  exec bash "${SCRIPT_DIR}/install.sh" "$@"
fi

tmp_installer="$(mktemp /tmp/virtuality-install.XXXXXX.sh)"
curl --connect-timeout 20 --max-time 180 -fsSL "$INSTALL_URL" -o "$tmp_installer"
exec bash "$tmp_installer" "$@"
