#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="/var/lib/virtuality/config"
PROFILE_FILE="${CONFIG_DIR}/host_profile.json"
ARCH="$(uname -m 2>/dev/null || echo unknown)"
MODEL="unknown"

if [[ -r /proc/device-tree/model ]]; then
  MODEL="$(tr -d '\0' </proc/device-tree/model | sed 's/[[:space:]]*$//')"
elif [[ -r /sys/firmware/devicetree/base/model ]]; then
  MODEL="$(tr -d '\0' </sys/firmware/devicetree/base/model | sed 's/[[:space:]]*$//')"
elif [[ -r /proc/cpuinfo ]]; then
  MODEL="$(awk -F: '/^(Model|Hardware|model name)/ {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit}' /proc/cpuinfo)"
  [[ -n "$MODEL" ]] || MODEL="unknown"
fi

LOWER_MODEL="$(echo "$MODEL" | tr '[:upper:]' '[:lower:]')"
PROFILE="x86_64"
LABEL="x86_64 KVM Node"
GUEST_ARCH="x86_64"
VM_MODE="x86_64-iso"
NETWORK="bridge"
PACKAGE_PROFILE="x86"

if [[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]]; then
  PROFILE="generic-arm64"
  LABEL="Generic ARM64 Edge Node"
  GUEST_ARCH="aarch64"
  VM_MODE="arm64-cloud-image"
  NETWORK="nat"
  PACKAGE_PROFILE="arm64"

  if [[ "$LOWER_MODEL" == *"raspberry pi"* ]]; then
    PROFILE="raspberry-arm64"
    LABEL="Raspberry Pi ARM64 Edge Node"
    PACKAGE_PROFILE="raspberry"
  elif [[ "$LOWER_MODEL" == *"orange pi 5"* || "$LOWER_MODEL" == *"orangepi 5"* || "$LOWER_MODEL" == *"rk3588"* || "$LOWER_MODEL" == *"rockchip"* ]]; then
    PROFILE="orangepi5-arm64"
    LABEL="Orange Pi 5 ARM64 Edge Node"
    PACKAGE_PROFILE="orangepi5"
  fi
fi

KVM_DEVICE="false"
[[ -e /dev/kvm ]] && KVM_DEVICE="true"

QEMU_X86="false"
command -v qemu-system-x86_64 >/dev/null 2>&1 && QEMU_X86="true"

QEMU_ARM="false"
command -v qemu-system-aarch64 >/dev/null 2>&1 && QEMU_ARM="true"

VIRT_INSTALL="false"
command -v virt-install >/dev/null 2>&1 && VIRT_INSTALL="true"

NFT="false"
command -v nft >/dev/null 2>&1 && NFT="true"

mkdir -p "$CONFIG_DIR"
cat > "$PROFILE_FILE" <<EOF
{
  "arch": "$ARCH",
  "model": "$MODEL",
  "profile": "$PROFILE",
  "label": "$LABEL",
  "package_profile": "$PACKAGE_PROFILE",
  "recommended_network": "$NETWORK",
  "recommended_guest_arch": "$GUEST_ARCH",
  "recommended_vm_mode": "$VM_MODE",
  "kvm_device": $KVM_DEVICE,
  "qemu_system_x86_64": $QEMU_X86,
  "qemu_system_aarch64": $QEMU_ARM,
  "virt_install": $VIRT_INSTALL,
  "nft": $NFT
}
EOF

printf '%s\n' "$PROFILE"
printf 'ARCH=%s\nMODEL=%s\nLABEL=%s\nPACKAGE_PROFILE=%s\nNETWORK=%s\nGUEST_ARCH=%s\nVM_MODE=%s\nPROFILE_FILE=%s\n' \
  "$ARCH" "$MODEL" "$LABEL" "$PACKAGE_PROFILE" "$NETWORK" "$GUEST_ARCH" "$VM_MODE" "$PROFILE_FILE" >&2
