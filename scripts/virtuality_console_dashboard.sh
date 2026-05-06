#!/usr/bin/env bash
set -euo pipefail

# Virtuality Console Dashboard
# btop-like compact TUI for physical server console tty1

refresh_interval="${VIRTUALITY_DASHBOARD_INTERVAL:-2}"

has_cmd() { command -v "$1" >/dev/null 2>&1; }

ESC="\033"
RESET="${ESC}[0m"
BOLD="${ESC}[1m"
DIM="${ESC}[2m"
FG_CYAN="${ESC}[38;5;51m"
FG_BLUE="${ESC}[38;5;39m"
FG_GREEN="${ESC}[38;5;46m"
FG_YELLOW="${ESC}[38;5;226m"
FG_RED="${ESC}[38;5;196m"
FG_MAGENTA="${ESC}[38;5;201m"
FG_GRAY="${ESC}[38;5;245m"
FG_WHITE="${ESC}[38;5;255m"
BG_PANEL="${ESC}[48;5;235m"
BG_HEADER="${ESC}[48;5;24m"

move() { printf "${ESC}[%s;%sH" "$1" "$2"; }
clear_screen() { printf "${ESC}[2J${ESC}[H"; }
hide_cursor() { printf "${ESC}[?25l"; }
show_cursor() { printf "${ESC}[?25h${RESET}"; }
trap show_cursor EXIT

term_cols() { tput cols 2>/dev/null || echo 120; }
term_rows() { tput lines 2>/dev/null || echo 40; }

repeat_char() {
  local char="$1" count="$2"
  printf '%*s' "$count" '' | tr ' ' "$char"
}

strip_ansi() {
  sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g'
}

print_fit() {
  local text="$1" width="$2"
  printf "%-${width}.${width}s" "$text"
}

box() {
  local row="$1" col="$2" height="$3" width="$4" title="$5"
  local inner=$((width - 2))
  move "$row" "$col"; printf "${FG_BLUE}╭%s╮${RESET}" "$(repeat_char '─' "$inner")"
  move "$row" $((col + 2)); printf "${BG_PANEL}${FG_CYAN}${BOLD} %s ${RESET}" "$title"
  for ((i=1; i<height-1; i++)); do
    move $((row+i)) "$col"; printf "${FG_BLUE}│${RESET}"
    printf "${BG_PANEL}%s${RESET}" "$(repeat_char ' ' "$inner")"
    printf "${FG_BLUE}│${RESET}"
  done
  move $((row+height-1)) "$col"; printf "${FG_BLUE}╰%s╯${RESET}" "$(repeat_char '─' "$inner")"
}

put() {
  local row="$1" col="$2" width="$3" text="$4"
  move "$row" "$col"
  printf "${BG_PANEL}"
  printf "%b" "$text" | cut -c 1-"$width"
  printf "${RESET}"
}

bar() {
  local percent="$1" width="$2"
  local filled=$(( percent * width / 100 ))
  local empty=$(( width - filled ))
  local color="$FG_GREEN"
  if (( percent >= 85 )); then color="$FG_RED"; elif (( percent >= 65 )); then color="$FG_YELLOW"; fi
  printf "%b%s%b%s" "$color" "$(repeat_char '█' "$filled")" "$DIM" "$(repeat_char '░' "$empty")"
  printf "%b" "$RESET"
}

service_state() {
  local unit="$1"
  if systemctl list-unit-files "$unit" >/dev/null 2>&1; then
    systemctl is-active "$unit" 2>/dev/null || echo "inactive"
  else
    echo "missing"
  fi
}

state_badge() {
  local state="$1"
  case "$state" in
    active) printf "${FG_GREEN}● active${RESET}" ;;
    inactive) printf "${FG_YELLOW}● inactive${RESET}" ;;
    failed) printf "${FG_RED}● failed${RESET}" ;;
    missing) printf "${FG_GRAY}● missing${RESET}" ;;
    *) printf "${FG_YELLOW}● %s${RESET}" "$state" ;;
  esac
}

get_cpu_usage() {
  local a b idle_a total_a idle_b total_b diff_idle diff_total usage
  read -r _ a b c idle_a rest < /proc/stat
  total_a=$((a+b+c+idle_a))
  sleep 0.15
  read -r _ a b c idle_b rest < /proc/stat
  total_b=$((a+b+c+idle_b))
  diff_idle=$((idle_b-idle_a))
  diff_total=$((total_b-total_a))
  if (( diff_total <= 0 )); then echo 0; else echo $(( (100 * (diff_total - diff_idle)) / diff_total )); fi
}

get_mem_percent() {
  free | awk '/Mem:/ {printf "%d", ($3/$2)*100}'
}

get_disk_percent() {
  df / | awk 'NR==2 {gsub("%", "", $5); print $5}'
}

while true; do
  cols="$(term_cols)"
  rows="$(term_rows)"
  [[ "$cols" -lt 100 ]] && cols=100
  [[ "$rows" -lt 32 ]] && rows=32

  cpu="$(get_cpu_usage)"
  mem="$(get_mem_percent)"
  disk="$(get_disk_percent)"
  load="$(awk '{print $1" "$2" "$3}' /proc/loadavg)"
  uptime_text="$(uptime -p 2>/dev/null | sed 's/up //' || true)"
  host="$(hostname)"
  now="$(date '+%Y-%m-%d %H:%M:%S')"
  ip_main="$(hostname -I 2>/dev/null | awk '{print $1}')"
  gateway="$(ip route | awk '/default/ {print $3" via "$5; exit}')"

  clear_screen
  hide_cursor

  move 1 1
  printf "${BG_HEADER}${FG_WHITE}${BOLD} %-20s ${FG_CYAN}%-30s ${FG_GRAY}%s ${RESET}" "VIRTUALITY" "$host" "$now"
  printf "%s" "$(repeat_char ' ' $((cols-70 > 0 ? cols-70 : 1)))"

  # Layout
  left_w=48
  right_w=$((cols - left_w - 5))
  [[ "$right_w" -lt 48 ]] && right_w=48

  box 3 2 10 "$left_w" "SYSTEM"
  box 3 $((left_w + 4)) 10 "$right_w" "SERVICES"
  box 14 2 8 "$left_w" "NETWORK"
  box 14 $((left_w + 4)) 8 "$right_w" "VIRTUAL MACHINES"
  box 23 2 8 "$left_w" "STORAGE"
  box 23 $((left_w + 4)) 8 "$right_w" "LIBVIRT POOLS"

  # System panel
  put 5 4 $((left_w-4)) "${FG_GRAY}CPU ${FG_WHITE}${cpu}%  $(bar "$cpu" 26)"
  put 6 4 $((left_w-4)) "${FG_GRAY}RAM ${FG_WHITE}${mem}%  $(bar "$mem" 26)"
  put 7 4 $((left_w-4)) "${FG_GRAY}DSK ${FG_WHITE}${disk}% $(bar "$disk" 26)"
  put 8 4 $((left_w-4)) "${FG_GRAY}Load:${RESET} ${FG_WHITE}${load}${RESET}"
  put 9 4 $((left_w-4)) "${FG_GRAY}Uptime:${RESET} ${FG_WHITE}${uptime_text}${RESET}"
  put 10 4 $((left_w-4)) "${FG_GRAY}Kernel:${RESET} ${FG_WHITE}$(uname -r)${RESET}"

  # Services panel
  svc_col=$((left_w + 6))
  svc_w=$((right_w - 4))
  put 5 "$svc_col" "$svc_w" "libvirtd.service        $(state_badge "$(service_state libvirtd.service)")"
  put 6 "$svc_col" "$svc_w" "virtlogd.service        $(state_badge "$(service_state virtlogd.service)")"
  put 7 "$svc_col" "$svc_w" "cockpit.socket          $(state_badge "$(service_state cockpit.socket)")"
  put 8 "$svc_col" "$svc_w" "libvirt-guests.service  $(state_badge "$(service_state libvirt-guests.service)")"
  put 10 "$svc_col" "$svc_w" "${FG_GRAY}Cockpit:${RESET} ${FG_WHITE}https://${ip_main}:9090${RESET}"

  # Network panel
  put 16 4 $((left_w-4)) "${FG_GRAY}Main IP:${RESET} ${FG_GREEN}${ip_main}${RESET}"
  put 17 4 $((left_w-4)) "${FG_GRAY}Gateway:${RESET} ${FG_WHITE}${gateway}${RESET}"
  net_lines="$(ip -br a 2>/dev/null | head -n 3)"
  n=0
  while IFS= read -r line; do
    put $((18+n)) 4 $((left_w-4)) "${FG_WHITE}${line}${RESET}"
    n=$((n+1))
  done <<< "$net_lines"

  # VM panel
  vm_col=$((left_w + 6))
  vm_lines="$(virsh list --all 2>/dev/null | tail -n +3 | sed '/^$/d' | head -n 4 || true)"
  if [[ -z "$vm_lines" ]]; then
    put 16 "$vm_col" "$svc_w" "${FG_GRAY}VM пока нет. Следующий этап — создать тестовую VM.${RESET}"
  else
    n=0
    while IFS= read -r line; do
      put $((16+n)) "$vm_col" "$svc_w" "${FG_WHITE}${line}${RESET}"
      n=$((n+1))
    done <<< "$vm_lines"
  fi

  # Storage panel
  srow=25
  for dir in /var/lib/virtuality/iso /var/lib/virtuality/images /var/lib/virtuality/backups /var/log/virtuality; do
    if [[ -d "$dir" ]]; then
      used="$(du -sh "$dir" 2>/dev/null | awk '{print $1}')"
      put "$srow" 4 $((left_w-4)) "${FG_GRAY}${dir}:${RESET} ${FG_WHITE}${used}${RESET}"
    else
      put "$srow" 4 $((left_w-4)) "${FG_GRAY}${dir}:${RESET} ${FG_RED}missing${RESET}"
    fi
    srow=$((srow+1))
  done

  # Pools panel
  pool_lines="$(virsh pool-list --all 2>/dev/null | tail -n +3 | sed '/^$/d' | head -n 4 || true)"
  if [[ -z "$pool_lines" ]]; then
    put 25 "$vm_col" "$svc_w" "${FG_GRAY}Storage pools unavailable${RESET}"
  else
    n=0
    while IFS= read -r line; do
      put $((25+n)) "$vm_col" "$svc_w" "${FG_WHITE}${line}${RESET}"
      n=$((n+1))
    done <<< "$pool_lines"
  fi

  move $((rows-2)) 2
  printf "${FG_GRAY}Refresh:${RESET} ${FG_WHITE}${refresh_interval}s${RESET}  ${FG_GRAY}|${RESET} ${FG_CYAN}Ctrl+Alt+F2${RESET} login  ${FG_GRAY}|${RESET} ${FG_GREEN}bt${RESET}=btop  ${FG_GRAY}|${RESET} stop: ${FG_YELLOW}sudo systemctl stop virtuality-console-dashboard${RESET}"
  move $((rows-1)) 1
  printf "${RESET}"
  sleep "$refresh_interval"
done
