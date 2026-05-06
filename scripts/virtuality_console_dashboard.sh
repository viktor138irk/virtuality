#!/usr/bin/env bash
set -euo pipefail

refresh_interval="${VIRTUALITY_DASHBOARD_INTERVAL:-5}"

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

service_state() {
  local unit="$1"
  if systemctl list-unit-files "$unit" >/dev/null 2>&1; then
    systemctl is-active "$unit" 2>/dev/null || echo "inactive"
  else
    echo "missing"
  fi
}

while true; do
  clear
  echo "============================================================"
  echo "                    VIRTUALITY NODE DASHBOARD"
  echo "============================================================"
  echo "Date:      $(date '+%Y-%m-%d %H:%M:%S')"
  echo "Hostname:  $(hostname)"
  echo "Uptime:    $(uptime -p 2>/dev/null || true)"
  echo "Kernel:    $(uname -r)"
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    echo "OS:        ${PRETTY_NAME}"
  fi
  echo

  echo "[SYSTEM]"
  echo "Load:      $(awk '{print $1, $2, $3}' /proc/loadavg)"
  echo "CPU virt:  $(grep -E -q '(vmx|svm)' /proc/cpuinfo && echo OK || echo WARNING)"
  free -h | awk '/Mem:/ {print "Memory:   used " $3 " / total " $2}'
  df -h / | awk 'NR==2 {print "Root FS:  used " $3 " / total " $2 " (" $5 ")"}'
  echo

  echo "[NETWORK]"
  ip -br a 2>/dev/null | sed 's/^/  /' || true
  echo "Gateway:   $(ip route | awk '/default/ {print $3 " via " $5; exit}')"
  echo

  echo "[VIRTUALITY SERVICES]"
  printf "  %-24s %s\n" "libvirtd.service" "$(service_state libvirtd.service)"
  printf "  %-24s %s\n" "virtlogd.service" "$(service_state virtlogd.service)"
  printf "  %-24s %s\n" "cockpit.socket" "$(service_state cockpit.socket)"
  printf "  %-24s %s\n" "libvirt-guests.service" "$(service_state libvirt-guests.service)"
  echo

  echo "[LIBVIRT STORAGE POOLS]"
  if has_cmd virsh; then
    virsh pool-list --all 2>/dev/null | sed 's/^/  /' || echo "  virsh pool-list unavailable"
  else
    echo "  virsh not installed"
  fi
  echo

  echo "[VIRTUAL MACHINES]"
  if has_cmd virsh; then
    virsh list --all 2>/dev/null | sed 's/^/  /' || echo "  virsh list unavailable"
  else
    echo "  virsh not installed"
  fi
  echo

  echo "[STORAGE PATHS]"
  for dir in /var/lib/virtuality/iso /var/lib/virtuality/images /var/lib/virtuality/backups /var/log/virtuality; do
    if [[ -d "$dir" ]]; then
      used="$(du -sh "$dir" 2>/dev/null | awk '{print $1}')"
      printf "  %-34s %s\n" "$dir" "$used"
    else
      printf "  %-34s %s\n" "$dir" "missing"
    fi
  done
  echo

  echo "[FIREWALL]"
  if has_cmd ufw; then
    ufw status 2>/dev/null | head -n 12 | sed 's/^/  /' || true
  else
    echo "  ufw not installed"
  fi
  echo

  echo "============================================================"
  echo "Refresh: ${refresh_interval}s | btop: press Ctrl+Alt+F2 login and run bt"
  echo "Stop dashboard temporarily: sudo systemctl stop virtuality-console-dashboard"
  echo "============================================================"
  sleep "$refresh_interval"
done
