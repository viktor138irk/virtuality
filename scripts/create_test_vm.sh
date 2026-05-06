#!/usr/bin/env bash
set -euo pipefail

VM_NAME="${1:-test-alpine}"
ISO_URL="${VIRTUALITY_TEST_ISO_URL:-https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/x86_64/alpine-standard-3.20.3-x86_64.iso}"
ISO_PATH="/var/lib/virtuality/iso/alpine-standard.iso"
DISK_PATH="/var/lib/virtuality/images/${VM_NAME}.qcow2"
BRIDGE="${VIRTUALITY_BRIDGE:-br0}"
MEMORY="${VIRTUALITY_TEST_VM_MEMORY:-1024}"
VCPUS="${VIRTUALITY_TEST_VM_VCPUS:-1}"
DISK_SIZE="${VIRTUALITY_TEST_VM_DISK_SIZE:-8}"

if [[ "$EUID" -ne 0 ]]; then
  echo "Ошибка: запусти через sudo: sudo bash scripts/create_test_vm.sh"
  exit 1
fi

if ! command -v virt-install >/dev/null 2>&1; then
  echo "Ошибка: virt-install не найден. Запусти install_virtuality_node.sh"
  exit 1
fi

if ! command -v virsh >/dev/null 2>&1; then
  echo "Ошибка: virsh не найден. Запусти install_virtuality_node.sh"
  exit 1
fi

if ! ip link show "$BRIDGE" >/dev/null 2>&1; then
  echo "Ошибка: bridge $BRIDGE не найден. Сначала настрой br0."
  ip -br a
  exit 1
fi

mkdir -p /var/lib/virtuality/iso /var/lib/virtuality/images

if virsh dominfo "$VM_NAME" >/dev/null 2>&1; then
  echo "Виртуальная машина $VM_NAME уже существует."
  virsh list --all
  exit 0
fi

if [[ ! -f "$ISO_PATH" ]]; then
  echo "Скачиваю тестовый ISO: $ISO_URL"
  wget -O "$ISO_PATH" "$ISO_URL"
fi

virsh pool-refresh virtuality-iso >/dev/null 2>&1 || true
virsh pool-refresh virtuality-images >/dev/null 2>&1 || true

echo "Создаю тестовую VM: $VM_NAME"
virt-install \
  --name "$VM_NAME" \
  --memory "$MEMORY" \
  --vcpus "$VCPUS" \
  --disk "path=${DISK_PATH},size=${DISK_SIZE},format=qcow2,bus=virtio" \
  --cdrom "$ISO_PATH" \
  --os-variant generic \
  --network "bridge=${BRIDGE},model=virtio" \
  --graphics vnc,listen=0.0.0.0 \
  --noautoconsole

echo
 echo "VM создана."
virsh list --all

echo
 echo "VNC display:"
virsh vncdisplay "$VM_NAME" || true

echo
 echo "Открыть в Cockpit: https://SERVER_IP:9090 → Virtual machines → ${VM_NAME}"
