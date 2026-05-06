#!/usr/bin/env bash
set -u

SOURCE_DIR="${VIRTUALITY_SOURCE_DIR:-/opt/virtuality/source}"
STATE_DIR="/var/lib/virtuality/update"
STATE_FILE="${STATE_DIR}/state.json"
LOG_FILE="/var/log/virtuality/update.log"
REMOTE="${VIRTUALITY_UPDATE_REMOTE:-origin}"
BRANCH="${VIRTUALITY_UPDATE_BRANCH:-main}"
REPO_ZIP_URL="${VIRTUALITY_UPDATE_ZIP_URL:-https://github.com/viktor138irk/virtuality/archive/refs/heads/${BRANCH}.zip}"
TMP_DIR="${STATE_DIR}/tmp"

mkdir -p "$STATE_DIR" "$TMP_DIR" "$(dirname "$LOG_FILE")"

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
try_run() {
  log "TRY: $*"
  "$@" >> "$LOG_FILE" 2>&1
  return $?
}

fetch_zip_update() {
  local zip_file extract_dir extracted_root
  zip_file="${TMP_DIR}/virtuality-${BRANCH}.zip"
  extract_dir="${TMP_DIR}/zip-update"
  rm -rf "$zip_file" "$extract_dir"
  mkdir -p "$extract_dir"

  log "Fallback update via ZIP started"
  log "ZIP URL: $REPO_ZIP_URL"
  write_state "running" "GitHub git fetch не сработал, пробуем ZIP fallback"

  if command -v curl >/dev/null 2>&1; then
    if ! try_run curl -L --connect-timeout 20 --max-time 240 --retry 3 --retry-delay 3 -o "$zip_file" "$REPO_ZIP_URL"; then
      log "curl ZIP download failed"
      return 1
    fi
  elif command -v wget >/dev/null 2>&1; then
    if ! try_run wget -T 240 -t 3 -O "$zip_file" "$REPO_ZIP_URL"; then
      log "wget ZIP download failed"
      return 1
    fi
  else
    log "ERROR: neither curl nor wget found"
    return 1
  fi

  if [ ! -s "$zip_file" ]; then
    log "ERROR: downloaded ZIP is empty: $zip_file"
    return 1
  fi

  if command -v unzip >/dev/null 2>&1; then
    if ! try_run unzip -q "$zip_file" -d "$extract_dir"; then
      log "unzip failed"
      return 1
    fi
  else
    log "ERROR: unzip not found"
    return 1
  fi

  extracted_root="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -1)"
  if [ -z "$extracted_root" ] || [ ! -f "$extracted_root/install.sh" ]; then
    log "ERROR: extracted repo root invalid: $extracted_root"
    return 1
  fi

  log "ZIP extracted root: $extracted_root"
  log "Syncing ZIP content into $SOURCE_DIR"
  mkdir -p "$SOURCE_DIR"
  if ! try_run rsync -a --delete --exclude='.git/' "$extracted_root/" "$SOURCE_DIR/"; then
    log "rsync from ZIP fallback failed"
    return 1
  fi

  log "ZIP fallback sync completed"
  return 0
}

STARTED_AT="$(now)"
write_state "running" "Обновление запущено"
log "============================================================"
log "Virtuality GitHub update started"
log "source: $SOURCE_DIR"
log "remote: $REMOTE"
log "branch: $BRANCH"
log "zip fallback: $REPO_ZIP_URL"

BEFORE_COMMIT="unknown"
BEFORE_VERSION="$(cat "$SOURCE_DIR/VERSION" 2>/dev/null || echo unknown)"
if [ -d "$SOURCE_DIR/.git" ]; then
  cd "$SOURCE_DIR" || exit 1
  BEFORE_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
fi
log "before commit: $BEFORE_COMMIT"
log "before version: $BEFORE_VERSION"

GIT_UPDATED=0
if [ -d "$SOURCE_DIR/.git" ]; then
  cd "$SOURCE_DIR" || exit 1
  write_state "running" "Получаем изменения из GitHub через git"
  if try_run git fetch "$REMOTE" "$BRANCH"; then
    TARGET_REF="${REMOTE}/${BRANCH}"
    TARGET_COMMIT="$(git rev-parse "$TARGET_REF" 2>/dev/null || true)"
    if [ -n "$TARGET_COMMIT" ]; then
      LOCAL_STATUS="$(git status --porcelain 2>/dev/null || true)"
      if [ -n "$LOCAL_STATUS" ]; then
        log "Local changes before update:"
        printf '%s\n' "$LOCAL_STATUS" >> "$LOG_FILE"
      fi
      write_state "running" "Синхронизируем исходники с GitHub через git reset"
      log "Deploy mode: git reset --hard $TARGET_REF"
      if try_run git reset --hard "$TARGET_REF" && try_run git clean -fd; then
        GIT_UPDATED=1
      else
        log "git reset/clean failed, ZIP fallback will be used"
      fi
    else
      log "remote ref not found after fetch: $TARGET_REF"
    fi
  else
    log "git fetch failed, ZIP fallback will be used"
  fi
else
  log "git repository not found, ZIP fallback will be used"
fi

if [ "$GIT_UPDATED" != "1" ]; then
  if ! fetch_zip_update; then
    write_state "error" "Не удалось обновиться ни через git, ни через ZIP fallback. Проверь доступ к github.com, curl/wget/unzip и DNS."
    exit 1
  fi
fi

AFTER_COMMIT="unknown"
if [ -d "$SOURCE_DIR/.git" ]; then
  cd "$SOURCE_DIR" || exit 1
  AFTER_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
fi
AFTER_VERSION="$(cat "$SOURCE_DIR/VERSION" 2>/dev/null || echo unknown)"
log "after commit: $AFTER_COMMIT"
log "after version: $AFTER_VERSION"

cd "$SOURCE_DIR" || exit 1
write_state "running" "Запускаем установщик web-панели"
run_step bash scripts/install_web_panel.sh

write_state "running" "Перезапускаем virtuality-web"
run_step systemctl restart virtuality-web

log "Virtuality GitHub update finished successfully"
write_state "success" "Обновление завершено: ${BEFORE_VERSION} → ${AFTER_VERSION}"
exit 0
