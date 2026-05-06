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

(
  flock -n 9 || {
    log "another update check is already running, skip"
    exit 0
  }

  STARTED_AT="$(now)"
  log "checking updates"

  if [ ! -d "$SOURCE_DIR/.git" ]; then
    log "git repository not found: $SOURCE_DIR"
    write_state "error" "Автообновление: не найден git-репозиторий: $SOURCE_DIR"
    exit 0
  fi

  cd "$SOURCE_DIR" || {
    log "cannot cd to source dir: $SOURCE_DIR"
    write_state "error" "Автообновление: не удалось открыть $SOURCE_DIR"
    exit 0
  }

  current="$(git rev-parse HEAD 2>/dev/null || true)"
  if [ -z "$current" ]; then
    log "cannot read current commit"
    write_state "error" "Автообновление: не удалось прочитать текущий commit"
    exit 0
  fi

  if ! git fetch --quiet "$REMOTE" "$BRANCH" >> "$LOG_FILE" 2>&1; then
    log "git fetch failed"
    write_state "error" "Автообновление: git fetch завершился ошибкой"
    exit 0
  fi

  latest="$(git rev-parse "${REMOTE}/${BRANCH}" 2>/dev/null || true)"
  if [ -z "$latest" ]; then
    log "cannot read remote commit ${REMOTE}/${BRANCH}"
    write_state "error" "Автообновление: не удалось прочитать удалённый commit ${REMOTE}/${BRANCH}"
    exit 0
  fi

  if [ "$current" = "$latest" ]; then
    log "no updates: ${current:0:12}"
    write_state "idle" "Автообновление: новых обновлений нет"
    exit 0
  fi

  if [ ! -f "$APPLY_SCRIPT" ]; then
    log "apply script not found: $APPLY_SCRIPT"
    write_state "error" "Автообновление: не найден скрипт установки обновления"
    exit 0
  fi

  log "update found: ${current:0:12} -> ${latest:0:12}"
  write_state "running" "Автообновление: найдено обновление, запускаем установку"
  VIRTUALITY_SOURCE_DIR="$SOURCE_DIR" VIRTUALITY_UPDATE_REMOTE="$REMOTE" VIRTUALITY_UPDATE_BRANCH="$BRANCH" bash "$APPLY_SCRIPT"
) 9>"$LOCK_FILE"
