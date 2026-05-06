#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${VIRTUALITY_APP_DIR:-/opt/virtuality/web}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISK_IMAGES_DIR="/var/lib/virtuality/disk-images"

mkdir -p "$DISK_IMAGES_DIR"
chmod 755 "$DISK_IMAGES_DIR"

if [[ -f "${REPO_DIR}/scripts/patch_disk_images.py" ]]; then
  python3 "${REPO_DIR}/scripts/patch_disk_images.py" "${APP_DIR}/app.py"
else
  echo "patch_disk_images.py not found" >&2
  exit 1
fi

systemctl restart virtuality-web.service

echo "Disk image support installed. Open: /disk-images"
