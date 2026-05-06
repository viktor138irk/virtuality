#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo " Virtuality Node Diagnostics"
echo "=================================================="

echo
 echo "[System]"
if [[ -f /etc/os-release ]]; then
  . /etc/os-release
  echo "OS: ${PRETTY_NAME}"
fi
uname -a

echo
 echo "[CPU virtualization]"
if grep -E -q '(vmx|svm)' /proc/cpuinfo; then
  echo "OK: hardware virtualization flag found"
else
  echo "WARNING: vmx/svm flag not found"
fi

echo
 echo "[Services]"
for service in libvirtd virtlogd cockpit.socket; do
  if systemctl list-unit-files | grep -q "^${service}"; then
    echo -n "${service}: "
    systemctl is-active "$service" || true
  else
    echo "${service}: not installed"
  fi
done

echo
 echo "[Packages]"
for bin in virsh qemu-system-x86_64 virt-install cockpit-bridge; do
  if command -v "$bin" >/dev/null 2>&1; then
    echo "OK: $bin -> $(command -v "$bin")"
  else
    echo "MISSING: $bin"
  fi
done

echo
 echo "[libvirt VMs]"
if command -v virsh >/dev/null 2>&1; then
  virsh list --all || true
else
  echo "virsh not found"
fi

echo
 echo "[libvirt storage pools]"
if command -v virsh >/dev/null 2>&1; then
  virsh pool-list --all || true
else
  echo "virsh not found"
fi

echo
 echo "[Network interfaces]"
ip -br a || true

echo
 echo "[Routes]"
ip route || true

echo
 echo "[Virtuality paths]"
for dir in /opt/virtuality /var/lib/virtuality /var/lib/virtuality/iso /var/lib/virtuality/images /var/lib/virtuality/backups /var/log/virtuality; do
  if [[ -d "$dir" ]]; then
    echo "OK: $dir"
  else
    echo "MISSING: $dir"
  fi
done

echo
 echo "[Firewall]"
if command -v ufw >/dev/null 2>&1; then
  ufw status || true
else
  echo "ufw not installed"
fi

echo
 echo "=================================================="
echo " Diagnostics completed"
echo "=================================================="
