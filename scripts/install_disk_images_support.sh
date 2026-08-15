#!/usr/bin/env bash
set -euo pipefail

DISK_IMAGES_DIR="/var/lib/virtuality/disk-images"

mkdir -p "$DISK_IMAGES_DIR"
chmod 755 "$DISK_IMAGES_DIR"

systemctl restart virtuality-web.service

echo "Disk image support installed. Open: /disk-images"
