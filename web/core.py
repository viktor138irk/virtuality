"""Shared primitives of the Virtuality panel: settings, auth, command runner,
background operations store, templates. Feature modules import from here;
app.py re-exports the same names for backwards compatibility."""
import json
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer, URLSafeTimedSerializer

import auth
import presenters
import update_core

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = "Virtuality"
ENV_FILE = BASE_DIR / ".env"
STORAGE_DIR = Path("/var/lib/virtuality")
ISO_DIR = Path("/var/lib/virtuality/iso")
IMAGES_DIR = Path("/var/lib/virtuality/images")
DISK_IMAGES_DIR = Path("/var/lib/virtuality/disk-images")
BACKUPS_DIR = Path("/var/lib/virtuality/backups")
CONFIG_DIR = Path("/var/lib/virtuality/config")
OPERATIONS_DIR = Path("/var/log/virtuality/operations")
DEFAULT_BRIDGE = "br0"
IMAGES_POOL = "virtuality-images"
ISO_POOL = "virtuality-iso"

OP_LOCK = threading.Lock()


def load_env() -> dict[str, str]:
    data: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw_line in ENV_FILE.read_text().splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip().strip('"').strip("'")
    return data


CONFIG = load_env()
AUTH_USER = CONFIG.get("VIRTUALITY_AUTH_USER", os.environ.get("VIRTUALITY_AUTH_USER", "root"))
SESSION_SECRET = CONFIG.get("VIRTUALITY_SESSION_SECRET", os.environ.get("VIRTUALITY_SESSION_SECRET", "dev-secret-change-me"))
COOKIE_SECURE = CONFIG.get("VIRTUALITY_COOKIE_SECURE", os.environ.get("VIRTUALITY_COOKIE_SECURE", "0")) == "1"
SESSION_MAX_AGE = 60 * 60 * 12
login_throttle = auth.LoginThrottle()
serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="virtuality-session")
console_serializer = URLSafeSerializer(SESSION_SECRET, salt="virtuality-console")


def is_configured() -> bool:
    return bool(AUTH_USER and SESSION_SECRET != "dev-secret-change-me")


def read_version() -> str:
    for path in (BASE_DIR / "VERSION", BASE_DIR.parent / "VERSION"):
        try:
            return path.read_text().strip() or "unknown"
        except OSError:
            continue
    return "unknown"


APP_VERSION = read_version()


# ---------------------------------------------------------------- templates
def update_notice() -> dict[str, Any]:
    try:
        data = json.loads((update_core.STATE_DIR / "last_check.json").read_text())
    except Exception:
        return {"has_update": False}
    return {"has_update": bool(data.get("has_update")), "latest_version": data.get("latest_version", "")}


templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals.update(
    update_notice=update_notice,
    vm_presets=presenters.VM_PRESETS,
    port_presets=presenters.PORT_PRESETS,
    plural=presenters.plural,
    app_version=APP_VERSION,
    app_name=APP_NAME,
)
templates.env.filters.update(
    vm_state=presenters.vm_state,
    operation_state=presenters.operation_state,
    service_state=presenters.service_state,
    format_mb=presenters.format_mb,
    format_bytes=presenters.format_bytes,
    level_tone=presenters.level_tone,
    port_service=presenters.port_service,
)


def render(request: Request, template: str, context: dict[str, Any] | None = None, status_code: int = 200, **extra: Any):
    data = {"request": request, "app_name": APP_NAME, "user": AUTH_USER}
    data.update(context or {})
    data.update(extra)
    return templates.TemplateResponse(template, data, status_code=status_code)


# ---------------------------------------------------------------- auth
def verify_linux_password(username: str, password: str) -> bool:
    if username != AUTH_USER:
        return False
    return auth.verify_password(username, password)


def user_from_session_token(token: str | None) -> str | None:
    if not token:
        return None
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    return AUTH_USER if data.get("user") == AUTH_USER else None


def get_current_user(request: Request) -> str | None:
    return user_from_session_token(request.cookies.get("virtuality_session"))


def require_auth(request: Request):
    """Return a redirect to /login for anonymous requests, otherwise None."""
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


def require_api_auth(request: Request):
    """JSON variant of require_auth for /api and /live endpoints."""
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    return None


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin or origin == "null":
        # Browsers always send Origin on cross-site form posts; tools like curl do not.
        return origin != "null"
    origin_host = urlsplit(origin).netloc.lower()
    allowed = {request.headers.get("host", "").lower()}
    forwarded = request.headers.get("x-forwarded-host")
    if forwarded:
        allowed.update(item.strip().lower() for item in forwarded.split(","))
    return origin_host in allowed


def redirect_with_message(path: str, key: str, message: str) -> RedirectResponse:
    return RedirectResponse(url=f"{path}?{key}={quote(message)}", status_code=303)


# ---------------------------------------------------------------- commands
def run_cmd(cmd: list[str], timeout: int = 12) -> dict[str, Any]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return {"ok": result.returncode == 0, "code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "cmd": " ".join(cmd)}
    except Exception as exc:
        return {"ok": False, "code": -1, "stdout": "", "stderr": str(exc), "cmd": " ".join(cmd)}


def cmd_error(result: dict[str, Any], fallback: str) -> str:
    """Best human-readable error text from a run_cmd result."""
    text = (result.get("stderr") or result.get("stdout") or "").strip()
    text = re.sub(r"^error:\s*", "", text, flags=re.IGNORECASE)
    return text.splitlines()[0] if text else fallback


def utc_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def tail_text(path: Path, max_lines: int = 220) -> str:
    if not path.exists():
        return ""
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-max_lines:])
    except OSError:
        return ""


def valid_vm_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,62}", name or ""))


def valid_label(name: str) -> bool:
    """Snapshot, backup and similar user-chosen names."""
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", name or ""))


def vm_exists(name: str) -> bool:
    return run_cmd(["virsh", "dominfo", name], timeout=8)["ok"]


def vm_state(name: str) -> str:
    result = run_cmd(["virsh", "domstate", name], timeout=8)
    return (result.get("stdout") or "unknown").strip().lower()


def vm_is_running(name: str) -> bool:
    return vm_state(name) in ("running", "idle", "blocked", "paused", "pmsuspended", "in shutdown")


# ---------------------------------------------------------------- operations
def operation_meta_path(operation_id: str) -> Path:
    return OPERATIONS_DIR / f"{operation_id}.json"


def operation_log_path(operation_id: str) -> Path:
    return OPERATIONS_DIR / f"{operation_id}.log"


def ensure_operations_dir() -> None:
    OPERATIONS_DIR.mkdir(parents=True, exist_ok=True)


def write_operation(operation: dict[str, Any]) -> None:
    ensure_operations_dir()
    path = operation_meta_path(operation["id"])
    tmp_path = path.with_suffix(".json.tmp")
    with OP_LOCK:
        tmp_path.write_text(json.dumps(operation, ensure_ascii=False, indent=2))
        tmp_path.replace(path)


def read_operation(operation_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[a-f0-9-]{36}", operation_id or ""):
        return None
    path = operation_meta_path(operation_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    data["log_tail"] = tail_text(operation_log_path(operation_id))
    return data


def list_operations(limit: int = 25) -> list[dict[str, Any]]:
    ensure_operations_dir()
    operations: list[dict[str, Any]] = []
    for path in sorted(OPERATIONS_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text())
            data["log_tail"] = tail_text(operation_log_path(data["id"]), max_lines=20)
            operations.append(data)
        except Exception:
            continue
        if len(operations) >= limit:
            break
    return operations


def running_operations(kind: str | None = None) -> list[dict[str, Any]]:
    return [op for op in list_operations(50) if op.get("status") in ("queued", "running") and (kind is None or op.get("type") == kind)]


def append_operation_log(operation_id: str, message: str) -> None:
    ensure_operations_dir()
    with operation_log_path(operation_id).open("a", encoding="utf-8") as handle:
        handle.write(f"[{utc_now()}] {message.rstrip()}\n")


def operation_css(status: str) -> str:
    return "ok" if status == "success" else "err" if status == "error" else "warn"


def update_operation(operation: dict[str, Any], **changes: Any) -> None:
    operation.update(changes)
    operation["updated_at"] = utc_now()
    write_operation(operation)


def new_operation(kind: str, title: str, message: str = "Операция поставлена в очередь", **meta: Any) -> dict[str, Any]:
    operation = {"id": str(uuid.uuid4()), "type": kind, "title": title, "status": "queued", "progress": 0, "message": message, "created_at": utc_now(), "updated_at": utc_now(), "created_by": AUTH_USER}
    operation.update(meta)
    write_operation(operation)
    append_operation_log(operation["id"], "Операция поставлена в очередь.")
    return operation


def finish_operation(operation: dict[str, Any], ok: bool, message: str, **meta: Any) -> None:
    fresh = read_operation(operation["id"]) or operation
    fresh.pop("log_tail", None)
    update_operation(fresh, status="success" if ok else "error", progress=100, message=message, finished_at=utc_now(), **meta)
    append_operation_log(operation["id"], message)


def run_operation(operation: dict[str, Any], worker: Callable[[dict[str, Any]], None]) -> None:
    """Run worker(operation) in a daemon thread; the worker reports progress via
    update_operation/append_operation_log and must call finish_operation."""

    def runner() -> None:
        try:
            update_operation(operation, status="running", started_at=utc_now())
            worker(operation)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the user
            append_operation_log(operation["id"], f"Ошибка: {exc}")
            finish_operation(operation, False, str(exc)[:240])

    threading.Thread(target=runner, daemon=True).start()
