#!/usr/bin/env bash
set -u

SOURCE_DIR="${VIRTUALITY_SOURCE_DIR:-/opt/virtuality/source}"
STATE_DIR="/var/lib/virtuality/update"
STATE_FILE="${STATE_DIR}/state.json"
LOCK_FILE="${STATE_DIR}/auto_update.lock"
LOG_FILE="/var/log/virtuality/update.log"
REMOTE="${VIRTUALITY_UPDATE_REMOTE:-origin}"
BRANCH="${VIRTUALITY_UPDATE_BRANCH:-main}"
APPLY_SCRIPT="${SOURCE_DIR}/scripts/apply_github_update.sh"
ZIP_URL="${VIRTUALITY_UPDATE_ZIP_URL:-https://github.com/viktor138irk/virtuality/archive/refs/heads/${BRANCH}.zip}"

mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")"

now() { date '+%Y-%m-%d %H:%M:%S'; }
json_escape() { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read())[1:-1])'; }
log() { echo "[$(now)] [auto] $*" | tee -a "$LOG_FILE"; }
write_state() {
  local status="$1"
  local message="$2"
  local escaped
  escaped="$(printf '%s' "$message" | json_escape)"
  cat > "$STATE_FILE" <<JSON
{
  "status": "$status",
  "message": "$escaped",
  "started_at": "${STARTED_AT:-}",
  "updated_at": "$(now)",
  "finished_at": "$([ "$status" = running ] && echo "" || now)",
  "auto_update": true
}
JSON
}

diag_network() {
  log "diagnostics: host=$(hostname 2>/dev/null || echo unknown)"
  log "diagnostics: date=$(date -Is 2>/dev/null || date)"
  if command -v getent >/dev/null 2>&1; then
    getent hosts github.com >> "$LOG_FILE" 2>&1 || log "diagnostics: DNS github.com failed"
  fi
  if command -v git >/dev/null 2>&1; then
    git --version >> "$LOG_FILE" 2>&1 || true
    git -C "$SOURCE_DIR" remote -v >> "$LOG_FILE" 2>&1 || true
  else
    log "diagnostics: git not found"
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -I -L --connect-timeout 10 --max-time 25 https://github.com/ >> "$LOG_FILE" 2>&1 || log "diagnostics: curl github.com failed"
  else
    log "diagnostics: curl not found"
  fi
  if command -v unzip >/dev/null 2>&1; then
    unzip -v | head -2 >> "$LOG_FILE" 2>&1 || true
  else
    log "diagnostics: unzip not found"
  fi
}

(
  flock -n 9 || {
    log "another update check is already running, skip"
    exit 0
  }

  STARTED_AT="$(now)"
  log "checking updates"

  if [ ! -d "$SOURCE_DIR" ]; then
    log "source directory not found: $SOURCE_DIR"
    write_state "error" "Автообновление: не найдена директория исходников: $SOURCE_DIR"
    exit 0
  fi

  cd "$SOURCE_DIR" || {
    log "cannot cd to source dir: $SOURCE_DIR"
    write_state "error" "Автообновление: не удалось открыть $SOURCE_DIR"
    exit 0
  }

  current="unknown"
  if [ -d "$SOURCE_DIR/.git" ]; then
    current="$(git rev-parse HEAD 2>/dev/null || true)"
  fi
  current_version="$(cat VERSION 2>/dev/null || echo unknown)"

  if [ -z "$current" ]; then
    current="unknown"
  fi

  if [ -d "$SOURCE_DIR/.git" ]; then
    if git fetch --quiet "$REMOTE" "$BRANCH" >> "$LOG_FILE" 2>&1; then
      latest="$(git rev-parse "${REMOTE}/${BRANCH}" 2>/dev/null || true)"
      if [ -n "$latest" ] && [ "$current" = "$latest" ]; then
        log "no updates: ${current:0:12} / version $current_version"
        write_state "idle" "Автообновление: новых обновлений нет"
        exit 0
      fi
      log "update may be available or current commit unknown: ${current:0:12} -> ${latest:0:12}"
    else
      log "git fetch failed; diagnostics and apply ZIP fallback will be used"
      diag_network
    fi
  else
    log "git repository not found; apply ZIP fallback will be used"
    diag_network
  fi

  if [ ! -f "$APPLY_SCRIPT" ]; then
    log "apply script not found: $APPLY_SCRIPT"
    write_state "error" "Автообновление: не найден скрипт установки обновления"
    exit 0
  fi

  write_state "running" "Автообновление: проверяем и устанавливаем обновление через устойчивый механизм"
  VIRTUALITY_SOURCE_DIR="$SOURCE_DIR" VIRTUALITY_UPDATE_REMOTE="$REMOTE" VIRTUALITY_UPDATE_BRANCH="$BRANCH" VIRTUALITY_UPDATE_ZIP_URL="$ZIP_URL" bash "$APPLY_SCRIPT"
) 9>"$LOCK_FILE"
