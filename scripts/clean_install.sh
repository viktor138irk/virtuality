#!/usr/bin/env bash
set -euo pipefail

# Virtuality clean install helper.
# Default mode preserves user data: VM disks, ISO files, uploaded disk images and backups.
# Use --purge-data only when you intentionally want to remove Virtuality data directories too.

PURGE_DATA=0
YES=0

for arg in "$@"; do
  case "$arg" in
    --purge-data) PURGE_DATA=1 ;;
    --yes|-y) YES=1 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ "$EUID" -ne 0 ]]; then
  exec sudo bash "$0" "$@"
fi

RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
BOLD="\033[1m"
RESET="\033[0m"

say() { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}!${RESET} $*"; }
remove_path() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    rm -rf "$path"
    say "removed: $path"
  else
    warn "not found: $path"
  fi
}

cat <<EOF
${BOLD}Virtuality clean install${RESET}

Will remove:
- /opt/virtuality/web
- /opt/virtuality/venv
- /etc/systemd/system/virtuality-web.service
- /var/lib/virtuality/config
- /var/lib/virtuality/update
- /var/log/virtuality/update.log and install logs

Will preserve by default:
- /opt/virtuality/source
- /var/lib/virtuality/iso
- /var/lib/virtuality/images
- /var/lib/virtuality/disk-images
- /var/lib/virtuality/backups
- existing libvirt VMs and storage pools
EOF

if [[ "$PURGE_DATA" -eq 1 ]]; then
  cat <<EOF

${RED}${BOLD}PURGE DATA MODE ENABLED${RESET}
Will also remove:
- /var/lib/virtuality/iso
- /var/lib/virtuality/images
- /var/lib/virtuality/disk-images
- /var/lib/virtuality/backups
- /var/lib/virtuality/network

Existing libvirt VM definitions are not deleted automatically.
EOF
fi

if [[ "$YES" -ne 1 ]]; then
  echo
  read -r -p "Continue? Type YES: " answer
  if [[ "$answer" != "YES" ]]; then
    echo "Cancelled."
    exit 0
  fi
fi

systemctl stop virtuality-web.service 2>/dev/null || true
systemctl disable virtuality-web.service 2>/dev/null || true

remove_path /etc/systemd/system/virtuality-web.service
systemctl daemon-reload || true

remove_path /opt/virtuality/web
remove_path /opt/virtuality/venv
remove_path /var/lib/virtuality/config
remove_path /var/lib/virtuality/update
remove_path /var/log/virtuality/update.log
find /var/log/virtuality -maxdepth 1 -type f -name 'install_web_panel_*.log' -delete 2>/dev/null || true
say "old installer logs removed"

if [[ "$PURGE_DATA" -eq 1 ]]; then
  remove_path /var/lib/virtuality/iso
  remove_path /var/lib/virtuality/images
  remove_path /var/lib/virtuality/disk-images
  remove_path /var/lib/virtuality/backups
  remove_path /var/lib/virtuality/network
fi

mkdir -p /opt/virtuality /var/log/virtuality /var/lib/virtuality
say "base directories recreated"

echo
say "Clean install cleanup finished. Run: sudo bash scripts/install_web_panel.sh"
