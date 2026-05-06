#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-enp2s0}"
BRIDGE="br0"
NETPLAN_DIR="/etc/netplan"
BACKUP_DIR="/root/virtuality-netplan-backups/$(date +%Y%m%d_%H%M%S)"
BRIDGE_FILE="${NETPLAN_DIR}/60-virtuality-br0.yaml"
ROLLBACK_SCRIPT="/root/virtuality-rollback-netplan.sh"

if [[ "$EUID" -ne 0 ]]; then
  echo "Ошибка: запусти через sudo: sudo bash scripts/setup_bridge_br0.sh"
  exit 1
fi

if ! command -v netplan >/dev/null 2>&1; then
  echo "Ошибка: netplan не найден. Скрипт рассчитан на Ubuntu/Debian с netplan."
  exit 1
fi

if ! ip link show "$IFACE" >/dev/null 2>&1; then
  echo "Ошибка: интерфейс $IFACE не найден."
  ip -br a
  exit 1
fi

echo "============================================================"
echo "Virtuality bridge setup"
echo "============================================================"
echo "Interface: $IFACE"
echo "Bridge:    $BRIDGE"
echo "Backup:    $BACKUP_DIR"
echo

mkdir -p "$BACKUP_DIR"
cp -a "$NETPLAN_DIR"/*.yaml "$BACKUP_DIR"/ 2>/dev/null || true

cat > "$ROLLBACK_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cp -a "$BACKUP_DIR"/*.yaml "$NETPLAN_DIR"/
rm -f "$BRIDGE_FILE"
netplan generate
netplan apply
echo "Netplan rollback applied from $BACKUP_DIR"
EOF
chmod +x "$ROLLBACK_SCRIPT"

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

# Disable DHCP on the original cloud-init ethernet file, if it exists.
# We keep the file but neutralize enp2s0 to avoid duplicate DHCP on iface and bridge.
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
  fi
done

echo "[1/4] Generated config:"
cat "$BRIDGE_FILE"

echo
 echo "[2/4] Validating netplan..."
netplan generate

echo
 echo "[3/4] Applying netplan..."
netplan apply
sleep 4

echo
 echo "[4/4] Result:"
ip -br a
ip route

echo
 echo "============================================================"
echo "Bridge setup completed"
echo "============================================================"
echo "Rollback command if network is broken:"
echo "sudo $ROLLBACK_SCRIPT"
echo "============================================================"
