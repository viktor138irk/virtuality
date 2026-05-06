#!/usr/bin/env bash
set -u

SOURCE_DIR="${VIRTUALITY_SOURCE_DIR:-/opt/virtuality/source}"
STATE_DIR="/var/lib/virtuality/update"
STATE_FILE="${STATE_DIR}/state.json"
LOG_FILE="/var/log/virtuality/update.log"
REMOTE="${VIRTUALITY_UPDATE_REMOTE:-origin}"
BRANCH="${VIRTUALITY_UPDATE_BRANCH:-main}"

mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")"

now() { date '+%Y-%m-%d %H:%M:%S'; }
json_escape() { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read())[1:-1])'; }
write_state() {
  local status="$1"
  local message="$2"
  local escaped
  escaped="$(printf '%s' "$message" | json_escape)"
  cat > "$STATE_FILE" <<JSON
{
  "status": "$status",
  "message": "$escaped",
  "started_at": "${STARTED_AT:-$(now)}",
  "updated_at": "$(now)",
  "finished_at": "$([ "$status" = running ] && echo "" || now)"
}
JSON
}
log() { echo "[$(now)] $*" | tee -a "$LOG_FILE"; }
run_step() {
  log "RUN: $*"
  "$@" >> "$LOG_FILE" 2>&1
  local code=$?
  if [ $code -ne 0 ]; then
    log "ERROR: command failed with exit code $code: $*"
    write_state "error" "Команда завершилась с ошибкой: $*"
    exit $code
  fi
}

STARTED_AT="$(now)"
write_state "running" "Обновление запущено"
log "============================================================"
log "Virtuality GitHub update started"
log "source: $SOURCE_DIR"
log "remote: $REMOTE"
log "branch: $BRANCH"

if [ ! -d "$SOURCE_DIR/.git" ]; then
  log "ERROR: git repository not found: $SOURCE_DIR"
  write_state "error" "Не найден git-репозиторий: $SOURCE_DIR"
  exit 1
fi

cd "$SOURCE_DIR" || exit 1

BEFORE_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
BEFORE_VERSION="$(cat VERSION 2>/dev/null || echo unknown)"
log "before commit: $BEFORE_COMMIT"
log "before version: $BEFORE_VERSION"

write_state "running" "Получаем изменения из GitHub"
run_step git fetch "$REMOTE" "$BRANCH"

TARGET_REF="${REMOTE}/${BRANCH}"
TARGET_COMMIT="$(git rev-parse "$TARGET_REF" 2>/dev/null || true)"
if [ -z "$TARGET_COMMIT" ]; then
  log "ERROR: remote ref not found: $TARGET_REF"
  write_state "error" "Не найден удалённый ref: $TARGET_REF"
  exit 1
fi

LOCAL_STATUS="$(git status --porcelain 2>/dev/null || true)"
if [ -n "$LOCAL_STATUS" ]; then
  log "Local changes before update:"
  printf '%s\n' "$LOCAL_STATUS" >> "$LOG_FILE"
fi

write_state "running" "Синхронизируем исходники с GitHub"
log "Deploy mode: git reset --hard $TARGET_REF"
run_step git reset --hard "$TARGET_REF"
run_step git clean -fd

AFTER_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
AFTER_VERSION="$(cat VERSION 2>/dev/null || echo unknown)"
log "after commit: $AFTER_COMMIT"
log "after version: $AFTER_VERSION"

write_state "running" "Запускаем установщик web-панели"
run_step bash scripts/install_web_panel.sh

write_state "running" "Перезапускаем virtuality-web"
run_step systemctl restart virtuality-web

log "Virtuality GitHub update finished successfully"
write_state "success" "Обновление завершено: ${BEFORE_VERSION} → ${AFTER_VERSION}"
exit 0
