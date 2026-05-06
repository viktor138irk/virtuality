#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-enp2s0}"
BRIDGE="br0"
NETPLAN_DIR="/etc/netplan"
BACKUP_DIR="/root/virtuality-netplan-backups/$(date +%Y%m%d_%H%M%S)"
BRIDGE_FILE="${NETPLAN_DIR}/60-virtuality-br0.yaml"
ROLLBACK_SCRIPT="/root/virtuality-rollback-netplan.sh"
MODE="${2:-dhcp}"

if [[ "$EUID" -ne 0 ]]; then
  echo "Ошибка: запусти через sudo: sudo bash scripts/setup_bridge_br0.sh enp2s0"
  exit 1
fi

if ! command -v netplan >/dev/null 2>&1; then
  echo "Ошибка: netplan не найден."
  exit 1
fi

if ! ip link show "$IFACE" >/dev/null 2>&1; then
  echo "Ошибка: интерфейс $IFACE не найден."
  ip -br a
  exit 1
fi

CURRENT_IP="$(ip -4 addr show "$IFACE" | awk '/inet / {print $2; exit}')"
CURRENT_GW="$(ip route | awk '/default/ {print $3; exit}')"

# Use stable IPv4 DNS by default. Link-local IPv6 DNS can break YAML/routes without scope id.
DNS_1="${VIRTUALITY_DNS_1:-10.0.0.1}"
DNS_2="${VIRTUALITY_DNS_2:-1.1.1.1}"

echo "============================================================"
echo "Virtuality safe bridge setup"
echo "============================================================"
echo "Interface:       $IFACE"
echo "Bridge:          $BRIDGE"
echo "Mode:            $MODE"
echo "Current IP:      ${CURRENT_IP:-unknown}"
echo "Current gateway: ${CURRENT_GW:-unknown}"
echo "DNS:             ${DNS_1}, ${DNS_2}"
echo "Backup:          $BACKUP_DIR"
echo

echo "ВАЖНО: скрипт использует netplan try."
echo "Если сеть пропадёт, netplan должен автоматически откатить изменения."
echo "Если видишь подтверждение — нажми Enter, только если SSH/сеть не отвалилась."
echo

mkdir -p "$BACKUP_DIR"
cp -a "$NETPLAN_DIR"/*.yaml "$BACKUP_DIR"/ 2>/dev/null || true

cat > "$ROLLBACK_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
rm -f "$BRIDGE_FILE"
cp -a "$BACKUP_DIR"/*.yaml "$NETPLAN_DIR"/
chmod 600 "$NETPLAN_DIR"/*.yaml 2>/dev/null || true
netplan generate
netplan apply
echo "Netplan rollback applied from $BACKUP_DIR"
EOF
chmod +x "$ROLLBACK_SCRIPT"

# Keep existing netplan files neutral for this interface to avoid duplicate DHCP/IP.
for file in "$NETPLAN_DIR"/*.yaml; do
  [[ "$file" == "$BRIDGE_FILE" ]] && continue
  if grep -q "${IFACE}:" "$file"; then
    cp -a "$file" "${file}.virtuality-before-br0"
    cat > "$file" <<EOF
network:
  version: 2
  ethernets:
    ${IFACE}:
      dhcp4: false
      dhcp6: false
EOF
    chmod 600 "$file"
  fi
done

if [[ "$MODE" == "static" ]]; then
  if [[ -z "${CURRENT_IP:-}" || -z "${CURRENT_GW:-}" ]]; then
    echo "Ошибка: не удалось определить текущий IP/gateway для static mode."
    echo "Откат: sudo $ROLLBACK_SCRIPT"
    exit 1
  fi
  cat > "$BRIDGE_FILE" <<EOF
network:
  version: 2
  ethernets:
    ${IFACE}:
      dhcp4: false
      dhcp6: false
  bridges:
    ${BRIDGE}:
      interfaces:
        - ${IFACE}
      addresses:
        - ${CURRENT_IP}
      routes:
        - to: default
          via: ${CURRENT_GW}
      nameservers:
        addresses:
          - ${DNS_1}
          - ${DNS_2}
      dhcp4: false
      dhcp6: false
      parameters:
        stp: false
        forward-delay: 0
EOF
else
  cat > "$BRIDGE_FILE" <<EOF
network:
  version: 2
  ethernets:
    ${IFACE}:
      dhcp4: false
      dhcp6: false
  bridges:
    ${BRIDGE}:
      interfaces:
        - ${IFACE}
      dhcp4: true
      dhcp6: false
      parameters:
        stp: false
        forward-delay: 0
EOF
fi

chmod 600 "$BRIDGE_FILE"
chmod 600 "$NETPLAN_DIR"/*.yaml 2>/dev/null || true

echo "[1/5] Generated config:"
cat "$BRIDGE_FILE"

echo
 echo "[2/5] Validating netplan..."
netplan generate

echo
 echo "[3/5] Testing config with netplan try..."
echo "Если после применения сеть работает — подтверди netplan try клавишей Enter."
echo "Если SSH зависнет — жди автооткат или используй физический монитор: sudo $ROLLBACK_SCRIPT"
netplan try --timeout 30

echo
 echo "[4/5] Applying final netplan..."
netplan apply
sleep 3

echo
 echo "[5/5] Result:"
ip -br a
ip route

echo
 echo "============================================================"
echo "Bridge setup completed"
echo "============================================================"
echo "Rollback command: sudo $ROLLBACK_SCRIPT"
echo "If DHCP mode failed, try static mode:"
echo "sudo bash scripts/setup_bridge_br0.sh ${IFACE} static"
echo "============================================================"
