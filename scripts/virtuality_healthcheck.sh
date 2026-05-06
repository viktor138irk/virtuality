#!/usr/bin/env bash
set -uo pipefail

# Virtuality Healthcheck
# One-command diagnostics for node, network, services, libvirt, storage and VM state.

ERRORS=0
WARNINGS=0

ESC="\033"
RESET="${ESC}[0m"
BOLD="${ESC}[1m"
DIM="${ESC}[2m"
GREEN="${ESC}[32m"
YELLOW="${ESC}[33m"
RED="${ESC}[31m"
CYAN="${ESC}[36m"
GRAY="${ESC}[90m"

ok() { echo -e "${GREEN}[OK]${RESET} $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET} $*"; WARNINGS=$((WARNINGS+1)); }
err() { echo -e "${RED}[ERROR]${RESET} $*"; ERRORS=$((ERRORS+1)); }
info() { echo -e "${CYAN}[INFO]${RESET} $*"; }
section() { echo; echo -e "${BOLD}${CYAN}== $* ==${RESET}"; }

has_cmd() { command -v "$1" >/dev/null 2>&1; }
service_exists() { systemctl list-unit-files "$1" >/dev/null 2>&1; }
service_active() { systemctl is-active "$1" >/dev/null 2>&1; }

print_kv() {
  printf "  ${GRAY}%-22s${RESET} %s\n" "$1" "$2"
}

check_service() {
  local unit="$1"
  local required="${2:-yes}"
  if service_exists "$unit"; then
    local state
    state="$(systemctl is-active "$unit" 2>/dev/null || echo inactive)"
    local enabled
    enabled="$(systemctl is-enabled "$unit" 2>/dev/null || echo disabled)"
    if [[ "$state" == "active" ]]; then
      ok "$unit active / $enabled"
    else
      if [[ "$required" == "yes" ]]; then
        err "$unit is $state / $enabled"
      else
        warn "$unit is $state / $enabled"
      fi
    fi
  else
    if [[ "$required" == "yes" ]]; then
      err "$unit not found"
    else
      warn "$unit not found"
    fi
  fi
}

check_socket_or_service() {
  local socket="$1"
  local service="$2"
  if service_exists "$socket" && service_active "$socket"; then
    ok "$socket active"
  elif service_exists "$service" && service_active "$service"; then
    ok "$service active"
  else
    err "neither $socket nor $service is active"
  fi
}

require_root_note() {
  if [[ "$EUID" -ne 0 ]]; then
    warn "running without root; some checks may be incomplete. Recommended: sudo vhealth"
  fi
}

section "Virtuality Healthcheck"
print_kv "Date" "$(date '+%Y-%m-%d %H:%M:%S')"
print_kv "Hostname" "$(hostname)"
if [[ -f /etc/os-release ]]; then
  . /etc/os-release
  print_kv "OS" "${PRETTY_NAME}"
fi
print_kv "Kernel" "$(uname -r)"
print_kv "Uptime" "$(uptime -p 2>/dev/null || true)"
require_root_note

section "CPU / Virtualization"
if grep -E -q '(vmx|svm)' /proc/cpuinfo; then
  ok "hardware virtualization flag found: vmx/svm"
else
  err "hardware virtualization flag not found; enable virtualization in BIOS/UEFI"
fi
if [[ -e /dev/kvm ]]; then
  ok "/dev/kvm exists"
else
  err "/dev/kvm missing"
fi

section "Required Commands"
for cmd in virsh qemu-system-x86_64 virt-install ip ss df free systemctl; do
  if has_cmd "$cmd"; then
    ok "$cmd -> $(command -v "$cmd")"
  else
    err "$cmd missing"
  fi
done
for cmd in btop jq tree ncdu; do
  if has_cmd "$cmd"; then
    ok "$cmd installed"
  else
    warn "$cmd not installed"
  fi
done

section "Services"
check_socket_or_service "cockpit.socket" "cockpit.service"
check_socket_or_service "libvirtd.socket" "libvirtd.service"
check_socket_or_service "virtlogd.socket" "virtlogd.service"
check_service "virtuality-console-dashboard.service" "no"
check_service "libvirt-guests.service" "no"
if service_exists "fwupd-refresh.service"; then
  state="$(systemctl is-active fwupd-refresh.service 2>/dev/null || echo inactive)"
  failed="$(systemctl is-failed fwupd-refresh.service 2>/dev/null || echo unknown)"
  if [[ "$failed" == "failed" ]]; then
    warn "fwupd-refresh.service failed; not critical for Virtuality"
  else
    ok "fwupd-refresh.service state: $state"
  fi
else
  warn "fwupd-refresh.service not found; firmware refresh checks skipped"
fi

section "Network"
if has_cmd ip; then
  echo "$(ip -br a)" | sed 's/^/  /'
  default_route="$(ip route | awk '/default/ {print $0; exit}')"
  if [[ -n "$default_route" ]]; then
    ok "default route: $default_route"
  else
    err "default route missing"
  fi

  if ip link show br0 >/dev/null 2>&1; then
    br_state="$(cat /sys/class/net/br0/operstate 2>/dev/null || echo unknown)"
    br_ip="$(ip -4 addr show br0 | awk '/inet / {print $2; exit}')"
    if [[ -n "$br_ip" ]]; then
      ok "br0 exists, state=$br_state, ip=$br_ip"
    else
      warn "br0 exists but has no IPv4 address"
    fi
    if ip route | grep -q 'default .* br0'; then
      ok "default route uses br0"
    else
      warn "default route does not use br0"
    fi
  else
    warn "br0 not found; VMs may use NAT virbr0 only"
  fi

  if ip link show virbr0 >/dev/null 2>&1; then
    ok "virbr0 exists"
  else
    warn "virbr0 not found"
  fi
else
  err "ip command missing"
fi

section "Netplan"
if [[ -d /etc/netplan ]]; then
  ls -la /etc/netplan | sed 's/^/  /'
  if compgen -G "/etc/netplan/*.yaml" >/dev/null; then
    bad_perm=0
    for file in /etc/netplan/*.yaml; do
      perm="$(stat -c '%a' "$file" 2>/dev/null || echo unknown)"
      if [[ "$perm" != "600" ]]; then
        warn "$file permission is $perm, recommended 600"
        bad_perm=1
      fi
    done
    if [[ "$bad_perm" -eq 0 ]]; then
      ok "netplan yaml permissions look safe"
    fi
    if netplan generate >/tmp/virtuality-netplan-check.log 2>&1; then
      ok "netplan generate passed"
    else
      err "netplan generate failed"
      sed 's/^/  /' /tmp/virtuality-netplan-check.log
    fi
  else
    err "no netplan yaml files found"
  fi
else
  warn "/etc/netplan not found"
fi

section "Firewall"
if has_cmd ufw; then
  ufw_status="$(ufw status | head -n 1 || true)"
  if echo "$ufw_status" | grep -qi active; then
    ok "ufw active"
  else
    warn "ufw not active: $ufw_status"
  fi
  ufw status | sed 's/^/  /' | head -n 20
else
  warn "ufw not installed"
fi

section "Cockpit"
if ss -tulpn 2>/dev/null | grep -q ':9090'; then
  ok "port 9090 is listening"
else
  warn "port 9090 is not listening; cockpit may still be socket-activated"
fi
host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [[ -n "$host_ip" ]]; then
  info "Cockpit URL: https://${host_ip}:9090"
fi

section "libvirt"
if has_cmd virsh; then
  if virsh list --all >/tmp/virtuality-virsh-list.log 2>&1; then
    ok "virsh can list VMs"
    cat /tmp/virtuality-virsh-list.log | sed 's/^/  /'
  else
    err "virsh list failed"
    sed 's/^/  /' /tmp/virtuality-virsh-list.log
  fi

  if virsh pool-list --all >/tmp/virtuality-pool-list.log 2>&1; then
    ok "virsh can list storage pools"
    cat /tmp/virtuality-pool-list.log | sed 's/^/  /'
    if virsh pool-info virtuality-images >/dev/null 2>&1; then
      ok "pool virtuality-images exists"
    else
      warn "pool virtuality-images missing"
    fi
    if virsh pool-info virtuality-iso >/dev/null 2>&1; then
      ok "pool virtuality-iso exists"
    else
      warn "pool virtuality-iso missing"
    fi
  else
    err "virsh pool-list failed"
    sed 's/^/  /' /tmp/virtuality-pool-list.log
  fi

  if virsh net-list --all >/tmp/virtuality-net-list.log 2>&1; then
    ok "virsh can list networks"
    cat /tmp/virtuality-net-list.log | sed 's/^/  /'
  else
    warn "virsh net-list failed"
    sed 's/^/  /' /tmp/virtuality-net-list.log
  fi
else
  err "virsh missing"
fi

section "Virtuality Paths"
for dir in /opt/virtuality /var/lib/virtuality /var/lib/virtuality/iso /var/lib/virtuality/images /var/lib/virtuality/backups /var/log/virtuality; do
  if [[ -d "$dir" ]]; then
    size="$(du -sh "$dir" 2>/dev/null | awk '{print $1}')"
    ok "$dir exists, size=$size"
  else
    err "$dir missing"
  fi
done

section "Disk / Memory"
free -h | sed 's/^/  /'
df -h / /var/lib/virtuality 2>/dev/null | sed 's/^/  /'
root_use="$(df / | awk 'NR==2 {gsub("%", "", $5); print $5}')"
if [[ "$root_use" =~ ^[0-9]+$ ]]; then
  if (( root_use >= 90 )); then
    err "root filesystem usage is ${root_use}%"
  elif (( root_use >= 75 )); then
    warn "root filesystem usage is ${root_use}%"
  else
    ok "root filesystem usage is ${root_use}%"
  fi
fi

section "Summary"
if (( ERRORS > 0 )); then
  echo -e "${RED}${BOLD}Virtuality health: ERROR${RESET} — errors=${ERRORS}, warnings=${WARNINGS}"
  exit 2
elif (( WARNINGS > 0 )); then
  echo -e "${YELLOW}${BOLD}Virtuality health: WARNING${RESET} — warnings=${WARNINGS}"
  exit 1
else
  echo -e "${GREEN}${BOLD}Virtuality health: OK${RESET}"
  exit 0
fi
