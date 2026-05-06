#!/usr/bin/env bash
set -euo pipefail

# Virtuality Cockpit auth helper
# Checks Linux user password state, Cockpit disallowed-users and required groups.

USER_NAME="${1:-${VIRTUALITY_USER:-${SUDO_USER:-root}}}"
DISALLOWED_FILE="/etc/cockpit/disallowed-users"

ESC="\033"
RESET="${ESC}[0m"
BOLD="${ESC}[1m"
GREEN="${ESC}[32m"
YELLOW="${ESC}[33m"
RED="${ESC}[31m"
CYAN="${ESC}[36m"
GRAY="${ESC}[90m"

ok() { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}!${RESET} $*"; }
fail() { echo -e "${RED}✗${RESET} $*"; exit 1; }

if [[ "$EUID" -ne 0 ]]; then
  fail "Запусти от root: sudo bash scripts/fix_cockpit_auth.sh [user]"
fi

clear 2>/dev/null || true
cat <<EOF
${CYAN}${BOLD}Virtuality Cockpit auth check${RESET}
${GRAY}User:${RESET} ${USER_NAME}
EOF

echo

if ! id "$USER_NAME" >/dev/null 2>&1; then
  fail "Пользователь ${USER_NAME} не найден"
fi
ok "Пользователь найден: ${USER_NAME}"

HASH="$(getent shadow "$USER_NAME" | cut -d: -f2 || true)"
if [[ -z "$HASH" || "$HASH" == "!" || "$HASH" == "*" || "$HASH" == "!!" ]]; then
  warn "У пользователя ${USER_NAME} пароль не задан или заблокирован"
  echo
  echo -e "${BOLD}Для входа в Cockpit задай пароль:${RESET}"
  if [[ "$USER_NAME" == "root" ]]; then
    echo "  passwd root"
  else
    echo "  sudo passwd ${USER_NAME}"
  fi
else
  ok "У пользователя ${USER_NAME} есть пароль"
fi

if [[ -f "$DISALLOWED_FILE" ]]; then
  if grep -qx "$USER_NAME" "$DISALLOWED_FILE"; then
    cp "$DISALLOWED_FILE" "${DISALLOWED_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
    sed -i "/^${USER_NAME}$/d" "$DISALLOWED_FILE"
    ok "Пользователь ${USER_NAME} удалён из ${DISALLOWED_FILE}"
  else
    ok "Пользователь ${USER_NAME} не запрещён в ${DISALLOWED_FILE}"
  fi
else
  warn "Файл ${DISALLOWED_FILE} не найден, пропускаю проверку запрета пользователей"
fi

if [[ "$USER_NAME" != "root" ]]; then
  usermod -aG sudo "$USER_NAME" || true
  usermod -aG libvirt "$USER_NAME" || true
  usermod -aG kvm "$USER_NAME" || true
  ok "Пользователь ${USER_NAME} добавлен в группы sudo/libvirt/kvm"
  warn "Для применения групп нужно выйти из SSH и войти снова"
else
  ok "root не требует добавления в группы"
fi

if command -v apt >/dev/null 2>&1; then
  apt install -y cockpit cockpit-machines sscg >/dev/null 2>&1 || true
  ok "Пакеты cockpit/cockpit-machines/sscg проверены"
fi

systemctl enable --now cockpit.socket >/dev/null 2>&1 || fail "Не удалось запустить cockpit.socket"
ok "cockpit.socket включён и запущен"

if command -v ufw >/dev/null 2>&1; then
  ufw allow 9090/tcp >/dev/null 2>&1 || true
  ok "UFW правило 9090/tcp добавлено/проверено"
fi

systemctl restart cockpit.socket >/dev/null 2>&1 || true
systemctl restart cockpit.service >/dev/null 2>&1 || true
ok "Cockpit перезапущен"

SERVER_IP="$(hostname -I | awk '{print $1}')"
PUBLIC_IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')"

echo
echo -e "${GREEN}${BOLD}Готово.${RESET}"
echo -e "${BOLD}Cockpit local/private:${RESET} https://${SERVER_IP}:9090"
if [[ -n "$PUBLIC_IP" && "$PUBLIC_IP" != "$SERVER_IP" ]]; then
  echo -e "${BOLD}Cockpit route IP:${RESET}    https://${PUBLIC_IP}:9090"
fi
echo -e "${BOLD}Login:${RESET} ${USER_NAME}"
echo -e "${BOLD}Password:${RESET} пароль Linux-пользователя ${USER_NAME}"
echo
