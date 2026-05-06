#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="Virtuality"
PROJECT_DIR="/opt/virtuality"
STORAGE_DIR="/var/lib/virtuality"
ISO_DIR="${STORAGE_DIR}/iso"
IMAGES_DIR="${STORAGE_DIR}/images"
BACKUP_DIR="${STORAGE_DIR}/backups"
LOG_DIR="/var/log/virtuality"
COCKPIT_PORT="9090"

if [[ "$EUID" -ne 0 ]]; then
  echo "Ошибка: запусти от root: sudo bash install_virtuality_node.sh"
  exit 1
fi

if ! command -v apt >/dev/null 2>&1; then
  echo "Ошибка: установщик рассчитан на Debian/Ubuntu с apt."
  exit 1
fi

echo "=================================================="
echo " ${PROJECT_NAME} Node Installer v0.1"
echo "=================================================="

OS_NAME="$(. /etc/os-release && echo "${PRETTY_NAME}")"
echo "Система: ${OS_NAME}"

if grep -E -q '(vmx|svm)' /proc/cpuinfo; then
  echo "OK: CPU поддерживает аппаратную виртуализацию."
else
  echo "ВНИМАНИЕ: vmx/svm не найдено. Проверь виртуализацию в BIOS/UEFI."
fi

apt update
apt install -y \
  curl wget git nano htop unzip ca-certificates gnupg lsb-release \
  software-properties-common apt-transport-https ufw \
  qemu-kvm qemu-utils libvirt-daemon-system libvirt-clients virtinst \
  bridge-utils dnsmasq-base ovmf swtpm cloud-image-utils \
  cockpit cockpit-machines cockpit-networkmanager cockpit-storaged cockpit-packagekit

mkdir -p "$PROJECT_DIR" "$ISO_DIR" "$IMAGES_DIR" "$BACKUP_DIR" "$LOG_DIR"
chmod 755 "$PROJECT_DIR" "$STORAGE_DIR" "$ISO_DIR" "$IMAGES_DIR" "$BACKUP_DIR" "$LOG_DIR"

REAL_USER="${SUDO_USER:-root}"
if [[ "$REAL_USER" != "root" ]]; then
  usermod -aG libvirt "$REAL_USER" || true
  usermod -aG kvm "$REAL_USER" || true
fi

systemctl enable --now libvirtd
systemctl enable --now virtlogd
systemctl enable --now cockpit.socket

ufw allow OpenSSH || true
ufw allow "${COCKPIT_PORT}/tcp" || true
ufw allow 5900:5999/tcp || true
echo "y" | ufw enable || true

virsh pool-define-as virtuality-images dir --target "$IMAGES_DIR" >/dev/null 2>&1 || true
virsh pool-start virtuality-images >/dev/null 2>&1 || true
virsh pool-autostart virtuality-images >/dev/null 2>&1 || true

virsh pool-define-as virtuality-iso dir --target "$ISO_DIR" >/dev/null 2>&1 || true
virsh pool-start virtuality-iso >/dev/null 2>&1 || true
virsh pool-autostart virtuality-iso >/dev/null 2>&1 || true

cat > "${PROJECT_DIR}/virtuality.env" <<EOF
PROJECT_NAME="${PROJECT_NAME}"
PROJECT_DIR="${PROJECT_DIR}"
STORAGE_DIR="${STORAGE_DIR}"
ISO_DIR="${ISO_DIR}"
IMAGES_DIR="${IMAGES_DIR}"
BACKUP_DIR="${BACKUP_DIR}"
LOG_DIR="${LOG_DIR}"
COCKPIT_PORT="${COCKPIT_PORT}"
EOF

SERVER_IP="$(hostname -I | awk '{print $1}')"

echo "=================================================="
echo "Установка завершена"
echo "Cockpit: https://${SERVER_IP}:${COCKPIT_PORT}"
echo "ISO: ${ISO_DIR}"
echo "Images: ${IMAGES_DIR}"
echo "Backups: ${BACKUP_DIR}"
echo "=================================================="
