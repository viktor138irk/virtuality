#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Virtuality first boot setup
# Runs once on a system installed from a Virtuality image:
# installs KVM/libvirt/Cockpit, the web panel and helpers
# from the bundled source in /opt/virtuality/source.
# ==========================================================

SOURCE_DIR="${VIRTUALITY_SOURCE_DIR:-/opt/virtuality/source}"
IMAGE_ENV="/etc/virtuality/image.env"
STATE_DIR="/var/lib/virtuality"
DONE_FLAG="${STATE_DIR}/.firstboot-done"
LOG_DIR="/var/log/virtuality"
LOG_FILE="${LOG_DIR}/firstboot.log"
APT_LOCK_CONF="/etc/apt/apt.conf.d/90virtuality-lock-timeout"
ISSUE_FILE="/etc/issue.d/virtuality.issue"
NETWORK_WAIT_SECONDS="${VIRTUALITY_NETWORK_WAIT_SECONDS:-300}"

mkdir -p "$LOG_DIR" "$STATE_DIR" "$(dirname "$ISSUE_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

now() { date '+%Y-%m-%d %H:%M:%S'; }
say() { echo "[$(now)] [virtuality-firstboot] $*"; }

if [[ -f "$DONE_FLAG" ]]; then
  say "already completed, nothing to do"
  exit 0
fi

if [[ -f "$IMAGE_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$IMAGE_ENV"
  set +a
fi

first_human_user() {
  getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 && $7 !~ /(nologin|false)$/ {print $1; exit}'
}

AUTH_USER="${VIRTUALITY_USER:-$(first_human_user)}"
AUTH_USER="${AUTH_USER:-root}"
WEB_PORT="${VIRTUALITY_WEB_PORT:-8088}"
export VIRTUALITY_USER="$AUTH_USER"
export VIRTUALITY_AUTH_USER="$AUTH_USER"
export VIRTUALITY_WEB_PORT="$WEB_PORT"
export VIRTUALITY_AUTO_UPDATE="${VIRTUALITY_AUTO_UPDATE:-1}"
# Disk/RAM limits are reported by vhealth; they must not block an image install.
export VIRTUALITY_SKIP_REQUIREMENTS="${VIRTUALITY_SKIP_REQUIREMENTS:-1}"
export DEBIAN_FRONTEND=noninteractive
export TERM="${TERM:-dumb}"

cat > "$ISSUE_FILE" <<'EOF'
Virtuality: first boot setup is running. Progress: sudo journalctl -fu virtuality-firstboot

EOF

say "source: $SOURCE_DIR"
say "panel user: $AUTH_USER, port: $WEB_PORT"
[[ -f "$SOURCE_DIR/install_virtuality_node.sh" ]] || { say "ERROR: Virtuality source not found in $SOURCE_DIR"; exit 1; }

# unattended-upgrades often holds the dpkg lock right after the first boot.
printf 'DPkg::Lock::Timeout "900";\n' > "$APT_LOCK_CONF"

say "waiting for network (up to ${NETWORK_WAIT_SECONDS}s)"
deadline=$(( $(date +%s) + NETWORK_WAIT_SECONDS ))
until ip route show default 2>/dev/null | grep -q . && getent hosts archive.ubuntu.com >/dev/null 2>&1; do
  if (( $(date +%s) >= deadline )); then
    say "ERROR: no network with DNS. Configure networking; the setup will be retried automatically"
    exit 1
  fi
  sleep 5
done
say "network is ready"

cd "$SOURCE_DIR"
say "step 1/4: virtualization node (KVM, libvirt, Cockpit)"
bash ./install_virtuality_node.sh
say "step 2/4: healthcheck command"
bash ./scripts/install_healthcheck_command.sh
say "step 3/4: web panel"
bash ./scripts/install_web_panel.sh
say "step 4/4: console dashboard"
bash ./scripts/install_console_dashboard.sh

touch "$DONE_FLAG"
cat > "$ISSUE_FILE" <<EOF
Virtuality web panel: http://\4:${WEB_PORT}  (login: ${AUTH_USER})
Cockpit:              https://\4:9090

EOF
systemctl disable virtuality-firstboot.service >/dev/null 2>&1 || true
say "completed. Web panel: http://$(hostname -I | awk '{print $1}'):${WEB_PORT} login: ${AUTH_USER}"
