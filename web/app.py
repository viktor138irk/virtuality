#!/usr/bin/env python3
import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import lzma
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
import xml.etree.ElementTree as ET

from fastapi import FastAPI, Request, Form, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer, URLSafeTimedSerializer

import auth
import host_profile
import network_core
import presenters
import update_core
from network_core import NetworkError

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = "Virtuality"
ENV_FILE = BASE_DIR / ".env"
STORAGE_DIR = Path("/var/lib/virtuality")
ISO_DIR = Path("/var/lib/virtuality/iso")
IMAGES_DIR = Path("/var/lib/virtuality/images")
DISK_IMAGES_DIR = Path("/var/lib/virtuality/disk-images")
OPERATIONS_DIR = Path("/var/log/virtuality/operations")
DEFAULT_BRIDGE = "br0"
NOVNC_DIR = next((p for p in [Path("/usr/share/novnc"), Path("/usr/share/novnc/app")] if p.exists()), None)

app = FastAPI(title="Virtuality Panel")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
if NOVNC_DIR:
    app.mount("/novnc", StaticFiles(directory=str(NOVNC_DIR)), name="novnc")
OP_LOCK = threading.Lock()


def update_notice() -> dict[str, Any]:
    try:
        data = json.loads((update_core.STATE_DIR / "last_check.json").read_text())
    except Exception:
        return {"has_update": False}
    return {"has_update": bool(data.get("has_update")), "latest_version": data.get("latest_version", "")}


templates.env.globals.update(update_notice=update_notice, vm_presets=presenters.VM_PRESETS, port_presets=presenters.PORT_PRESETS)
templates.env.filters.update(
    vm_state=presenters.vm_state,
    operation_state=presenters.operation_state,
    service_state=presenters.service_state,
    format_mb=presenters.format_mb,
    level_tone=presenters.level_tone,
    port_service=presenters.port_service,
)
templates.env.globals["plural"] = presenters.plural

SECURITY_HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not same_origin(request):
        return JSONResponse({"ok": False, "error": "Cross-origin request blocked"}, status_code=403)
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


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


def verify_linux_password(username: str, password: str) -> bool:
    if username != AUTH_USER:
        return False
    return auth.verify_password(username, password)


def read_version() -> str:
    for path in (BASE_DIR / "VERSION", BASE_DIR.parent / "VERSION"):
        try:
            return path.read_text().strip() or "unknown"
        except OSError:
            continue
    return "unknown"


APP_VERSION = read_version()
templates.env.globals["app_version"] = APP_VERSION


def redirect_with_message(path: str, key: str, message: str) -> RedirectResponse:
    return RedirectResponse(url=f"{path}?{key}={quote(message)}", status_code=303)


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
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


def run_cmd(cmd: list[str], timeout: int = 12) -> dict[str, Any]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return {"ok": result.returncode == 0, "code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "cmd": " ".join(cmd)}
    except Exception as exc:
        return {"ok": False, "code": -1, "stdout": "", "stderr": str(exc), "cmd": " ".join(cmd)}


def utc_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def operation_meta_path(operation_id: str) -> Path:
    return OPERATIONS_DIR / f"{operation_id}.json"


def operation_log_path(operation_id: str) -> Path:
    return OPERATIONS_DIR / f"{operation_id}.log"


def ensure_operations_dir() -> None:
    OPERATIONS_DIR.mkdir(parents=True, exist_ok=True)


def tail_text(path: Path, max_lines: int = 220) -> str:
    if not path.exists():
        return ""
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-max_lines:])
    except OSError:
        return ""


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


def progress_from_line(current: int, line: str) -> int:
    low = line.lower()
    if "allocating" in low or "creating storage" in low:
        return max(current, 30)
    if "starting install" in low or "installing" in low:
        return max(current, 50)
    if "creating domain" in low:
        return max(current, 75)
    if "domain creation completed" in low or "installation continues" in low:
        return max(current, 90)
    return current


def run_operation_worker(operation_id: str, cmd: list[str]) -> None:
    operation = read_operation(operation_id)
    if not operation:
        return
    update_operation(operation, status="running", progress=10, message="virt-install запущен", started_at=utc_now())
    append_operation_log(operation_id, "Запуск команды:")
    append_operation_log(operation_id, " ".join(cmd))
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        if process.stdout:
            for line in process.stdout:
                append_operation_log(operation_id, line)
                fresh = read_operation(operation_id) or operation
                new_progress = progress_from_line(int(fresh.get("progress", 10)), line)
                if new_progress != fresh.get("progress"):
                    update_operation(fresh, progress=new_progress, message=line.strip()[:240] or fresh.get("message"))
        exit_code = process.wait()
        fresh = read_operation(operation_id) or operation
        if exit_code == 0:
            append_operation_log(operation_id, "virt-install завершился успешно.")
            run_cmd(["virsh", "pool-refresh", "virtuality-images"], timeout=20)
            update_operation(fresh, status="success", progress=100, exit_code=exit_code, message="VM создана успешно", finished_at=utc_now())
        else:
            append_operation_log(operation_id, f"virt-install завершился с ошибкой. Exit code: {exit_code}")
            update_operation(fresh, status="error", progress=100, exit_code=exit_code, message=f"virt-install завершился с ошибкой: {exit_code}", finished_at=utc_now())
    except Exception as exc:
        fresh = read_operation(operation_id) or operation
        append_operation_log(operation_id, f"Ошибка запуска операции: {exc}")
        update_operation(fresh, status="error", progress=100, exit_code=-1, message=str(exc), finished_at=utc_now())


def start_background_operation(operation: dict[str, Any], cmd: list[str]) -> None:
    write_operation(operation)
    append_operation_log(operation["id"], "Операция поставлена в очередь.")
    threading.Thread(target=run_operation_worker, args=(operation["id"], cmd), daemon=True).start()


LOG_SOURCES = {
    "web": {"title": "Панель", "kind": "journal", "unit": "virtuality-web.service"},
    "update": {"title": "Обновления", "kind": "file", "path": "/var/log/virtuality/update.log"},
    "install": {"title": "Установка", "kind": "glob", "pattern": "/var/log/virtuality/install_web_panel_*.log"},
    "operations": {"title": "Задачи", "kind": "operations"},
    "auto-update": {"title": "Автообновление", "kind": "journal", "unit": "virtuality-auto-update.service"},
    "libvirtd": {"title": "Виртуализация", "kind": "journal", "unit": "libvirtd.service"},
    "virtlogd": {"title": "Журналы машин", "kind": "journal", "unit": "virtlogd.service"},
}


def read_log_source(source: str, lines: int = 220) -> dict[str, Any]:
    key = source if source in LOG_SOURCES else "web"
    cfg = LOG_SOURCES[key]
    lines = max(20, min(int(lines or 220), 2000))
    content = ""
    path = ""
    cmd = ""
    if cfg["kind"] == "journal":
        unit = cfg["unit"]
        cmd = f"journalctl -u {unit} -n {lines} --no-pager"
        result = run_cmd(["journalctl", "-u", unit, "-n", str(lines), "--no-pager"], timeout=15)
        content = result["stdout"] or result["stderr"] or "Лог пуст или journalctl недоступен"
    elif cfg["kind"] == "file":
        path = cfg["path"]
        cmd = f"tail -n {lines} {path}"
        content = tail_text(Path(path), max_lines=lines) or "Файл лога пока пуст или не найден"
    elif cfg["kind"] == "glob":
        pattern = cfg["pattern"]
        files = sorted(Path('/').glob(pattern.lstrip('/')), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
        if files:
            path = str(files[0])
            cmd = f"tail -n {lines} {path}"
            content = tail_text(files[0], max_lines=lines) or "Файл лога пуст"
        else:
            cmd = f"ls {pattern}"
            content = "Логи установки ещё не найдены"
    elif cfg["kind"] == "operations":
        cmd = f"tail -n {lines} /var/log/virtuality/operations/*.log"
        parts = []
        ensure_operations_dir()
        for item in sorted(OPERATIONS_DIR.glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            parts.append(f"===== {item.name} =====\n" + tail_text(item, max_lines=max(20, lines // 5)))
        content = "\n\n".join(parts) or "Журналы операций пока пусты"
    return {"key": key, "title": cfg["title"], "content": content, "path": path, "cmd": cmd, "lines": lines}


def vm_autostart_status(name: str) -> dict[str, str | bool]:
    result = run_cmd(["virsh", "dominfo", name], timeout=8)
    output = result.get("stdout", "") or ""
    match = re.search(r"^Autostart:\s*(.+)$", output, re.MULTILINE | re.IGNORECASE)
    raw = match.group(1).strip() if match else "unknown"
    enabled = raw.lower() in ("enable", "enabled", "yes", "on")
    label = "enabled" if enabled else "disabled" if raw != "unknown" else "unknown"
    css = "ok" if enabled else "warn"
    return {"enabled": enabled, "label": label, "css": css, "raw": raw}


def parse_virsh_list() -> list[dict[str, str]]:
    def clean_ip(ip: str) -> str:
        ip = (ip or "").split("/")[0].strip()
        if not ip or ip.startswith("127.") or ip.startswith("169.254.") or ip == "0.0.0.0":
            return ""
        return ip

    def manual_ip_map() -> dict[str, str]:
        path = Path("/var/lib/virtuality/network/vm_ips.json")
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): clean_ip(str(v)) for k, v in data.items() if clean_ip(str(v))}
        except Exception:
            pass
        return {}

    def vm_macs(name: str) -> list[str]:
        try:
            result = run_cmd(["virsh", "domiflist", name], timeout=8)
            if not result.get("ok"):
                return []
            return [mac.lower() for mac in re.findall(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", result.get("stdout", ""))]
        except Exception:
            return []

    def ip_from_domifaddr(name: str) -> str:
        try:
            result = run_cmd(["virsh", "domifaddr", name], timeout=8)
            if result.get("ok"):
                for ip in re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})/\d+", result.get("stdout", "")):
                    ip = clean_ip(ip)
                    if ip:
                        return ip
        except Exception:
            pass
        return ""

    def ip_from_network_core(name: str) -> str:
        try:
            resolved = network_core.resolve_vm_ip(name)
            if resolved:
                return clean_ip(str(resolved))
        except Exception:
            pass
        return ""

    def ip_from_dnsmasq_leases(macs: list[str]) -> str:
        if not macs:
            return ""
        try:
            for lease_file in Path("/var/lib/libvirt/dnsmasq").glob("*.leases"):
                for line in lease_file.read_text(errors="ignore").splitlines():
                    low = line.lower()
                    if not any(mac in low for mac in macs):
                        continue
                    parts = line.split()
                    if len(parts) >= 3:
                        ip = clean_ip(parts[2])
                        if ip:
                            return ip
        except Exception:
            pass
        return ""

    def ip_from_neighbor_tables(macs: list[str]) -> str:
        if not macs:
            return ""
        commands = [["ip", "neigh", "show"], ["ip", "neigh", "show", "dev", "virbr100"], ["ip", "neigh", "show", "dev", "br0"], ["arp", "-an"]]
        for cmd in commands:
            try:
                result = run_cmd(cmd, timeout=8)
                if not result.get("ok"):
                    continue
                for line in result.get("stdout", "").splitlines():
                    low = line.lower()
                    if not any(mac in low for mac in macs):
                        continue
                    for value in re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line):
                        ip = clean_ip(value)
                        if ip:
                            return ip
            except Exception:
                pass
        return ""

    manual_ips = manual_ip_map()

    def resolve_ip(name: str) -> str:
        if not name:
            return "—"
        if manual_ips.get(name):
            return manual_ips[name]
        macs = vm_macs(name)
        for resolver in (lambda: ip_from_domifaddr(name), lambda: ip_from_network_core(name), lambda: ip_from_dnsmasq_leases(macs), lambda: ip_from_neighbor_tables(macs)):
            try:
                ip = resolver()
                if ip:
                    return ip
            except Exception:
                pass
        return "—"

    result = run_cmd(["virsh", "list", "--all"])
    rows = []
    if not result["ok"]:
        return rows
    for line in result["stdout"].splitlines()[2:]:
        parts = line.strip().split(None, 2)
        if len(parts) == 3:
            vm_id, name, state = parts
        elif len(parts) == 2:
            vm_id, name, state = "-", parts[0], parts[1]
        else:
            continue
        info = presenters.parse_dominfo(run_cmd(["virsh", "dominfo", name], timeout=8).get("stdout", ""))
        enabled = info["autostart"]
        label = "enabled" if enabled else "disabled" if info["autostart_known"] else "unknown"
        rows.append({"id": vm_id, "name": name, "state": state, "autostart_enabled": enabled, "autostart_label": label, "autostart_css": "ok" if enabled else "warn", "vcpus": info["vcpus"], "memory_mb": info["memory_mb"]})
    for row in rows:
        row["ip"] = resolve_ip(row.get("name", ""))
        row["manual_ip"] = manual_ips.get(row.get("name", ""), "")
    return rows


def parse_pool_list() -> list[dict[str, str]]:
    result = run_cmd(["virsh", "pool-list", "--all"])
    rows = []
    if not result["ok"]:
        return rows
    for line in result["stdout"].splitlines()[2:]:
        parts = line.strip().split()
        if len(parts) >= 3:
            rows.append({"name": parts[0], "state": parts[1], "autostart": parts[2]})
    return rows


def list_iso_files() -> list[dict[str, str]]:
    ISO_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for item in sorted(ISO_DIR.glob("*.iso")):
        try:
            stat = item.stat()
            size_mb = stat.st_size / 1024 / 1024
            updated = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size_mb = 0
            updated = "unknown"
        files.append({"name": item.name, "path": str(item), "size": f"{size_mb:.1f} MB", "updated": updated})
    return files


def safe_upload_filename(filename: str, allowed_suffixes: tuple[str, ...], fallback_prefix: str) -> str | None:
    original = Path(filename or "").name.strip()
    suffix = Path(original).suffix.lower()
    if suffix not in allowed_suffixes:
        return None
    stem = Path(original).stem.strip()
    stem = re.sub(r"\s+", "-", stem)
    stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", stem)
    stem = stem.strip("._-")
    if not stem:
        stem = fallback_prefix
    stem = stem[:120]
    name = f"{stem}{suffix}"
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,180}\.[a-zA-Z0-9]{2,8}", name):
        name = f"{fallback_prefix}{suffix}"
    return name


def safe_iso_filename(filename: str) -> str | None:
    return safe_upload_filename(filename, (".iso",), "virtuality-iso")


def iso_path_by_name(name: str) -> Path | None:
    safe_name = safe_iso_filename(name)
    if not safe_name:
        return None
    path = (ISO_DIR / safe_name).resolve()
    if ISO_DIR.resolve() not in path.parents:
        return None
    return path


def refresh_iso_pool() -> None:
    run_cmd(["virsh", "pool-refresh", "virtuality-iso"], timeout=20)


def vm_arch_options() -> list[dict[str, str]]:
    return [
        {"value": "auto", "label": "Auto — по профилю хоста"},
        {"value": "x86_64", "label": "x86_64 / amd64"},
        {"value": "aarch64", "label": "ARM64 / aarch64"},
        {"value": "generic", "label": "Generic / no arch override"},
    ]


def normalize_guest_arch(value: str, profile: dict[str, Any]) -> str:
    value = (value or "auto").strip()
    if value == "auto":
        return str(profile.get("recommended_guest_arch") or "x86_64")
    if value in ("x86_64", "amd64"):
        return "x86_64"
    if value in ("aarch64", "arm64"):
        return "aarch64"
    if value == "generic":
        return "generic"
    return str(profile.get("recommended_guest_arch") or "x86_64")


def list_disk_image_files() -> list[dict[str, str]]:
    DISK_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for item in sorted(list(DISK_IMAGES_DIR.glob("*.img")) + list(DISK_IMAGES_DIR.glob("*.raw")) + list(DISK_IMAGES_DIR.glob("*.qcow2"))):
        try:
            stat = item.stat()
            size_gb = stat.st_size / 1024 / 1024 / 1024
            updated = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size_gb = 0
            updated = "unknown"
        files.append({"name": item.name, "path": str(item), "format": item.suffix.lower().lstrip('.'), "size": f"{size_gb:.2f} GB", "updated": updated})
    return files


def safe_disk_image_filename(filename: str) -> str | None:
    name = Path(filename or "").name.strip().replace(" ", "-")
    name = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
    if not name.lower().endswith((".img", ".raw", ".qcow2")):
        return None
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,180}\.(img|raw|qcow2)", name, re.IGNORECASE):
        return None
    return name


def disk_image_path_by_name(name: str) -> Path | None:
    safe_name = safe_disk_image_filename(name)
    if not safe_name:
        return None
    path = (DISK_IMAGES_DIR / safe_name).resolve()
    if DISK_IMAGES_DIR.resolve() not in path.parents:
        return None
    return path


def disk_image_format(path: Path) -> str:
    suffix = path.suffix.lower().lstrip('.')
    if suffix == 'qcow2':
        return 'qcow2'
    return 'raw'


def bridge_exists(name: str) -> bool:
    if not name or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", name):
        return False
    return run_cmd(["ip", "link", "show", name], timeout=5)["ok"]


def safe_disk_upload_filename(filename: str) -> str | None:
    original = Path(filename or "").name.strip()
    lower = original.lower()
    if lower.endswith(".tar.gz"):
        suffix = ".tar.gz"
        stem = original[:-7]
    elif lower.endswith(".img.xz"):
        suffix = ".img.xz"
        stem = original[:-7]
    elif lower.endswith(".tgz"):
        suffix = ".tgz"
        stem = original[:-4]
    else:
        suffix = Path(original).suffix.lower()
        stem = Path(original).stem
    if suffix not in (".img", ".raw", ".qcow2", ".img.xz", ".zip", ".tar.gz", ".tgz"):
        return None
    stem = re.sub(r"\s+", "-", stem.strip())
    stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", stem)
    stem = stem.strip("._-")[:120]
    if not stem:
        stem = "virtuality-disk"
    return f"{stem}{suffix}"


def disk_upload_is_archive(name: str) -> bool:
    lower = str(name or "").lower()
    return lower.endswith((".zip", ".tar.gz", ".tgz"))


def disk_upload_is_xz_image(name: str) -> bool:
    return str(name or "").lower().endswith(".img.xz")


def disk_upload_is_image(name: str) -> bool:
    lower = str(name or "").lower()
    return lower.endswith((".img", ".raw", ".qcow2", ".img.xz"))


def archive_member_is_safe(name: str) -> bool:
    if not name:
        return False
    p = Path(name)
    if p.is_absolute():
        return False
    return ".." not in p.parts


def archive_member_basename(name: str) -> str | None:
    return safe_disk_image_filename(Path(name).name)


def unique_disk_image_path(name: str) -> Path:
    DISK_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    safe = safe_disk_image_filename(name)
    if not safe:
        raise ValueError("Некорректное имя образа диска")
    target = DISK_IMAGES_DIR / safe
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(1, 1000):
        candidate = DISK_IMAGES_DIR / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise ValueError("Не удалось подобрать свободное имя файла")


def find_archive_disk_members(archive_path: Path) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    lower = archive_path.name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir() or not archive_member_is_safe(info.filename):
                    continue
                safe_name = archive_member_basename(info.filename)
                if safe_name:
                    members.append({"kind": "zip", "name": info.filename, "safe_name": safe_name, "size": int(info.file_size)})
    elif lower.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive_path, "r:gz") as archive:
            for info in archive.getmembers():
                if not info.isfile() or not archive_member_is_safe(info.name):
                    continue
                safe_name = archive_member_basename(info.name)
                if safe_name:
                    members.append({"kind": "tar", "name": info.name, "safe_name": safe_name, "size": int(info.size)})
    else:
        raise ValueError("Поддерживаются только .zip, .tar.gz и .tgz")
    return sorted(members, key=lambda item: item.get("size", 0), reverse=True)


def extract_disk_archive(archive_path: Path) -> list[Path]:
    members = find_archive_disk_members(archive_path)
    if not members:
        raise ValueError("В архиве не найдено .img, .raw или .qcow2 файлов")
    extracted: list[Path] = []
    lower = archive_path.name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            for member in members:
                target = unique_disk_image_path(member["safe_name"])
                with archive.open(member["name"], "r") as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                extracted.append(target)
    else:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in members:
                file_obj = archive.extractfile(member["name"])
                if file_obj is None:
                    continue
                target = unique_disk_image_path(member["safe_name"])
                with file_obj as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                extracted.append(target)
    return extracted


def extract_xz_disk_image(compressed_path: Path, safe_name: str | None = None) -> Path:
    source_name = safe_name or compressed_path.name
    if not source_name.lower().endswith(".img.xz"):
        raise ValueError("Поддерживаются только сжатые образы .img.xz")
    raw_name = source_name[:-3]
    target = unique_disk_image_path(raw_name)
    try:
        with lzma.open(compressed_path, "rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def disk_convert_target_path(source_path: Path) -> Path:
    if source_path.suffix.lower() == ".qcow2":
        return source_path
    target = source_path.with_suffix(".qcow2")
    if not target.exists():
        return target
    for index in range(1, 1000):
        candidate = source_path.with_name(f"{source_path.stem}-{index}.qcow2")
        if not candidate.exists():
            return candidate
    raise ValueError("Не удалось подобрать имя qcow2 для конвертации")


def disk_convert_progress(line: str, current: int) -> int:
    match = re.search(r"\((\d+(?:\.\d+)?)/100%\)", line or "")
    if match:
        return max(current, int(float(match.group(1))))
    match = re.search(r"(\d+(?:\.\d+)?)%", line or "")
    if match:
        return max(current, int(float(match.group(1))))
    return current


def run_disk_convert_worker(operation_id: str, source_path: str, target_path: str) -> None:
    operation = read_operation(operation_id)
    if not operation:
        return
    source = Path(source_path)
    target = Path(target_path)
    update_operation(operation, status="running", progress=1, message=f"Конвертация {source.name} в qcow2", started_at=utc_now())
    cmd = ["qemu-img", "convert", "-p", "-f", disk_image_format(source), "-O", "qcow2", str(source), str(target)]
    append_operation_log(operation_id, "Запуск конвертации:")
    append_operation_log(operation_id, " ".join(cmd))
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        progress = 1
        if process.stdout:
            for line in process.stdout:
                append_operation_log(operation_id, line)
                progress = disk_convert_progress(line, progress)
                fresh = read_operation(operation_id) or operation
                update_operation(fresh, progress=progress, message=f"Конвертация {source.name}: {progress}%")
        exit_code = process.wait()
        fresh = read_operation(operation_id) or operation
        if exit_code == 0:
            append_operation_log(operation_id, f"Конвертация завершена: {target}")
            update_operation(fresh, status="success", progress=100, exit_code=exit_code, message=f"Готово: {target.name}", finished_at=utc_now(), target_path=str(target))
        else:
            append_operation_log(operation_id, f"qemu-img завершился с ошибкой. Exit code: {exit_code}")
            target.unlink(missing_ok=True)
            update_operation(fresh, status="error", progress=100, exit_code=exit_code, message=f"qemu-img завершился с ошибкой: {exit_code}", finished_at=utc_now())
    except Exception as exc:
        fresh = read_operation(operation_id) or operation
        append_operation_log(operation_id, f"Ошибка конвертации: {exc}")
        target.unlink(missing_ok=True)
        update_operation(fresh, status="error", progress=100, exit_code=-1, message=str(exc), finished_at=utc_now())


def start_disk_convert_operation(source_path: Path) -> dict[str, Any] | None:
    if source_path.suffix.lower() == ".qcow2":
        return None
    target_path = disk_convert_target_path(source_path)
    operation_id = str(uuid.uuid4())
    operation = {
        "id": operation_id,
        "type": "disk_convert",
        "title": f"Конвертация {source_path.name}",
        "status": "queued",
        "progress": 0,
        "message": "Конвертация поставлена в очередь",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "created_by": AUTH_USER,
        "source_path": str(source_path),
        "target_path": str(target_path),
    }
    write_operation(operation)
    append_operation_log(operation_id, "Операция поставлена в очередь.")
    threading.Thread(target=run_disk_convert_worker, args=(operation_id, str(source_path), str(target_path)), daemon=True).start()
    return operation


def disk_upload_response(request: Request, payload: dict[str, Any]):
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", ""):
        return JSONResponse(payload)
    return RedirectResponse(url="/disk-images", status_code=303)


def valid_vm_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,62}", name or ""))


def vm_exists(name: str) -> bool:
    return run_cmd(["virsh", "dominfo", name], timeout=8)["ok"]


def vm_ip(name: str) -> str:
    result = run_cmd(["virsh", "domifaddr", name], timeout=8)
    if not result["ok"]:
        return "not available"
    match = re.search(r"(192\.168\.100\.\d+|\d+\.\d+\.\d+\.\d+)/", result["stdout"])
    return match.group(1) if match else "not available"


def vm_vnc_display(name: str) -> str:
    return run_cmd(["virsh", "vncdisplay", name], timeout=8)["stdout"] or "not available"


def vnc_display_to_port(display: str) -> int | None:
    value = (display or "").strip()
    if not value or value == "not available":
        return None
    match = re.search(r":(\d+)$", value)
    if not match:
        return None
    display_number = int(match.group(1))
    if display_number >= 5900:
        return display_number
    return 5900 + display_number


def console_info(name: str) -> dict[str, Any]:
    display = vm_vnc_display(name)
    port = vnc_display_to_port(display)
    has_novnc = bool(NOVNC_DIR and (NOVNC_DIR / "vnc.html").exists())
    token = None
    url = None
    if port and has_novnc:
        token = console_serializer.dumps({"vm": name, "port": port})
        url = f"/novnc/vnc.html?autoconnect=1&resize=scale&path=console/ws/{token}"
    return {"vm": name, "display": display, "port": port, "has_novnc": has_novnc, "novnc_dir": str(NOVNC_DIR) if NOVNC_DIR else "not installed", "url": url}


def vm_boot_order_label(value: str) -> str:
    labels = {item["value"]: item["label"] for item in vm_boot_order_options()}
    return labels.get(value or "auto", "Auto — по источнику VM")


def boot_order_to_devs(value: str) -> list[str]:
    value = normalize_boot_order(value, "disk_image")
    mapping = {
        "disk": ["hd"],
        "cdrom_disk": ["cdrom", "hd"],
        "disk_cdrom": ["hd", "cdrom"],
        "network_disk": ["network", "hd"],
    }
    return mapping.get(value, ["hd"])


def boot_devs_to_order(devs: list[str]) -> str:
    clean = [item for item in devs if item in ("hd", "cdrom", "network")]
    if clean[:2] == ["cdrom", "hd"]:
        return "cdrom_disk"
    if clean[:2] == ["hd", "cdrom"]:
        return "disk_cdrom"
    if clean[:2] == ["network", "hd"]:
        return "network_disk"
    if clean[:1] == ["hd"]:
        return "disk"
    return "auto"


def current_vm_boot_order(name: str) -> str:
    result = run_cmd(["virsh", "dumpxml", name], timeout=12)
    if not result.get("ok"):
        return "auto"
    try:
        root = ET.fromstring(result.get("stdout") or "")
    except Exception:
        return "auto"
    os_node = root.find("os")
    if os_node is None:
        return "auto"
    devs = []
    for boot in os_node.findall("boot"):
        dev = boot.attrib.get("dev", "").strip()
        if dev:
            devs.append(dev)
    return boot_devs_to_order(devs)


def apply_vm_boot_order(name: str, boot_order: str) -> tuple[bool, str]:
    if not valid_vm_name(name) or not vm_exists(name):
        return False, "VM не найдена."
    if boot_order not in ("auto", "disk", "cdrom_disk", "disk_cdrom", "network_disk"):
        return False, "Некорректный порядок загрузки VM."

    selected = normalize_boot_order(boot_order, "disk_image")
    result = run_cmd(["virsh", "dumpxml", name], timeout=15)
    if not result.get("ok"):
        return False, result.get("stderr") or "Не удалось получить XML VM."

    try:
        root = ET.fromstring(result.get("stdout") or "")
    except Exception as exc:
        return False, f"Не удалось разобрать XML VM: {exc}"

    os_node = root.find("os")
    if os_node is None:
        os_node = ET.SubElement(root, "os")

    for boot in list(os_node.findall("boot")):
        os_node.remove(boot)

    insert_at = 0
    for idx, child in enumerate(list(os_node)):
        if child.tag in ("type", "loader", "nvram", "firmware", "smbios", "bootmenu"):
            insert_at = idx + 1

    for dev in reversed(boot_order_to_devs(selected)):
        boot_node = ET.Element("boot", {"dev": dev})
        os_node.insert(insert_at, boot_node)

    xml_text = ET.tostring(root, encoding="unicode")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".xml", delete=False) as handle:
        handle.write(xml_text)
        tmp_name = handle.name
    try:
        define = run_cmd(["virsh", "define", tmp_name], timeout=30)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    if not define.get("ok"):
        return False, define.get("stderr") or "virsh define завершился ошибкой."
    return True, f"Порядок загрузки применён: {vm_boot_order_label(selected)}. Если VM запущена, изменение сработает после перезапуска."


def parse_dominfo_value(dominfo: str, label: str) -> str:
    for line in (dominfo or "").splitlines():
        if line.strip().lower().startswith(label.lower()):
            return line.split(":", 1)[1].strip()
    return ""


def kib_text_to_mb(value: str) -> int:
    match = re.search(r"(\d+)", value or "")
    if not match:
        return 0
    return max(0, int(int(match.group(1)) / 1024))


def current_vm_arch(name: str) -> str:
    result = run_cmd(["virsh", "dumpxml", name], timeout=12)
    if not result.get("ok"):
        return "unknown"
    try:
        root = ET.fromstring(result.get("stdout") or "")
    except Exception:
        return "unknown"
    os_type = root.find("os/type")
    return (os_type.attrib.get("arch") if os_type is not None else "") or "unknown"


def vm_runtime_state(name: str) -> str:
    result = run_cmd(["virsh", "domstate", name], timeout=8)
    return (result.get("stdout") or "unknown").strip().lower()


def vm_resource_settings(name: str) -> dict[str, Any]:
    dominfo = run_cmd(["virsh", "dominfo", name], timeout=10).get("stdout") or ""
    state = (parse_dominfo_value(dominfo, "State") or vm_runtime_state(name)).lower()
    vcpus_raw = parse_dominfo_value(dominfo, "CPU(s)")
    used_memory_raw = parse_dominfo_value(dominfo, "Used memory")
    max_memory_raw = parse_dominfo_value(dominfo, "Max memory")
    memory_mb = kib_text_to_mb(used_memory_raw) or kib_text_to_mb(max_memory_raw) or 1024
    try:
        vcpus = int(re.search(r"\d+", vcpus_raw or "1").group(0))
    except Exception:
        vcpus = 1
    return {
        "state": state,
        "is_shutoff": state in ("shut off", "shutoff", "shut-off"),
        "memory_mb": memory_mb,
        "vcpus": vcpus,
        "arch": current_vm_arch(name),
    }


def apply_vm_resources(name: str, memory_mb: int, vcpus: int, guest_arch: str) -> tuple[bool, str]:
    if not valid_vm_name(name) or not vm_exists(name):
        return False, "VM не найдена."
    resources = vm_resource_settings(name)
    if not resources.get("is_shutoff"):
        return False, "CPU/RAM/архитектуру можно менять только когда VM выключена. Сначала выключи VM."
    if memory_mb < 512 or memory_mb > 262144:
        return False, "RAM должна быть от 512 MB до 262144 MB."
    if vcpus < 1 or vcpus > 128:
        return False, "CPU должен быть от 1 до 128 vCPU."
    if guest_arch not in ("keep", "x86_64", "aarch64"):
        return False, "Некорректная архитектура VM."

    result = run_cmd(["virsh", "dumpxml", name], timeout=15)
    if not result.get("ok"):
        return False, result.get("stderr") or "Не удалось получить XML VM."
    try:
        root = ET.fromstring(result.get("stdout") or "")
    except Exception as exc:
        return False, f"Не удалось разобрать XML VM: {exc}"

    memory_kib = str(int(memory_mb) * 1024)
    for tag in ("memory", "currentMemory"):
        node = root.find(tag)
        if node is None:
            node = ET.SubElement(root, tag)
        node.text = memory_kib
        node.set("unit", "KiB")

    vcpu_node = root.find("vcpu")
    if vcpu_node is None:
        vcpu_node = ET.SubElement(root, "vcpu")
    vcpu_node.text = str(int(vcpus))
    vcpu_node.set("placement", "static")

    arch_changed = False
    if guest_arch != "keep":
        os_type = root.find("os/type")
        if os_type is None:
            os_node = root.find("os")
            if os_node is None:
                os_node = ET.SubElement(root, "os")
            os_type = ET.SubElement(os_node, "type")
            os_type.text = "hvm"
        old_arch = os_type.attrib.get("arch", "")
        if old_arch != guest_arch:
            os_type.set("arch", guest_arch)
            if guest_arch == "aarch64":
                os_type.set("machine", "virt")
            arch_changed = True

    xml_text = ET.tostring(root, encoding="unicode")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".xml", delete=False) as handle:
        handle.write(xml_text)
        tmp_name = handle.name
    try:
        define = run_cmd(["virsh", "define", tmp_name], timeout=30)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    if not define.get("ok"):
        return False, define.get("stderr") or "virsh define завершился ошибкой."

    message = f"Ресурсы VM применены: CPU {vcpus}, RAM {memory_mb} MB"
    if arch_changed:
        message += f", архитектура {guest_arch}. Важно: смена архитектуры может потребовать совместимый диск/загрузчик."
    return True, message


def vm_state(name: str) -> str:
    result = run_cmd(["virsh", "domstate", name], timeout=8)
    return (result.get("stdout") or "unknown").strip().lower()


def current_vm_iso(name: str) -> str:
    result = run_cmd(["virsh", "domblklist", name, "--details"], timeout=10)
    if not result.get("ok"):
        return ""
    for line in (result.get("stdout") or "").splitlines():
        if ".iso" not in line.lower():
            continue
        parts = line.split()
        if parts:
            return parts[-1]
    return ""


def detach_vm_iso(name: str) -> tuple[bool, str]:
    if not valid_vm_name(name) or not vm_exists(name):
        return False, "VM не найдена."
    xml = run_cmd(["virsh", "dumpxml", name], timeout=12)
    cdrom_targets: list[str] = []
    if xml.get("ok"):
        try:
            root = ET.fromstring(xml.get("stdout") or "")
            devices = root.find("devices")
            if devices is not None:
                for disk in devices.findall("disk"):
                    if disk.attrib.get("device") != "cdrom":
                        continue
                    target = disk.find("target")
                    dev = target.attrib.get("dev") if target is not None else ""
                    if dev:
                        cdrom_targets.append(dev)
        except Exception:
            pass
    if not cdrom_targets:
        cdrom_targets = ["sda", "hda", "sdb", "hdc"]

    running = vm_state(name) == "running"
    errors = []
    changed_any = False
    for target in cdrom_targets:
        commands = []
        if running:
            commands.append(["virsh", "detach-disk", name, target, "--live"])
        commands.append(["virsh", "detach-disk", name, target, "--config"])
        for cmd in commands:
            result = run_cmd(cmd, timeout=30)
            if result.get("ok"):
                changed_any = True
            elif result.get("stderr"):
                errors.append(result.get("stderr"))
    if changed_any:
        return True, "ISO был отмонтирован."
    return False, errors[-1] if errors else "Подключенный ISO не найден."


def mount_vm_iso(name: str, iso_path: str) -> tuple[bool, str]:
    if not valid_vm_name(name) or not vm_exists(name):
        return False, "VM не найдена."
    iso = Path(iso_path or "").resolve()
    try:
        iso_root = ISO_DIR.resolve()
    except Exception:
        iso_root = Path("/var/lib/virtuality/iso")
    if iso_root not in iso.parents or iso.suffix.lower() != ".iso" or not iso.exists():
        return False, "ISO должен быть существующим .iso файлом из /var/lib/virtuality/iso."

    detach_vm_iso(name)
    running = vm_state(name) == "running"
    base = ["virsh", "attach-disk", name, str(iso), "sda", "--type", "cdrom", "--mode", "readonly"]
    if running:
        live = run_cmd(base + ["--live"], timeout=30)
        if not live.get("ok"):
            return False, live.get("stderr") or "Не удалось подключить ISO к запущенной VM."
    config = run_cmd(base + ["--config"], timeout=30)
    if not config.get("ok"):
        return False, config.get("stderr") or "Не удалось сохранить ISO в конфигурации VM."
    return True, "ISO был смонтирован в VM. Если гостевая ОС его не увидела сразу, перезагрузи VM или обнови устройства внутри гостевой ОС."


def vm_details(name: str) -> dict[str, Any]:
    dominfo = run_cmd(["virsh", "dominfo", name], timeout=10)["stdout"]
    autostart = vm_autostart_status(name)
    details = {
        "name": name,
        "dominfo": dominfo,
        "vnc": vm_vnc_display(name),
        "ip": vm_ip(name),
        "disks": run_cmd(["virsh", "domblklist", name, "--details"], timeout=10)["stdout"],
        "interfaces": run_cmd(["virsh", "domiflist", name], timeout=10)["stdout"],
        "autostart": dominfo,
        "autostart_enabled": autostart["enabled"],
        "autostart_label": autostart["label"],
        "autostart_css": autostart["css"],
    }
    details["info"] = presenters.parse_dominfo(dominfo)
    details["disk_list"] = presenters.parse_domblklist(details["disks"])
    details["interface_list"] = presenters.parse_domiflist(details["interfaces"])
    return details


def system_summary() -> dict[str, str]:
    hostname = run_cmd(["hostname"])["stdout"]
    uptime = run_cmd(["uptime", "-p"])["stdout"]
    kernel = run_cmd(["uname", "-r"])["stdout"]
    ip_addr = run_cmd(["hostname", "-I"])["stdout"].split()
    ip_main = ip_addr[0] if ip_addr else "unknown"
    load = Path("/proc/loadavg").read_text().split()[:3]
    return {"hostname": hostname, "uptime": uptime, "kernel": kernel, "ip": ip_main, "load": " ".join(load), "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def service_state(unit: str) -> str:
    result = run_cmd(["systemctl", "is-active", unit])
    return result["stdout"] or "inactive"


def network_summary() -> dict[str, str]:
    return {"interfaces": run_cmd(["ip", "-br", "a"])["stdout"], "routes": run_cmd(["ip", "route"])["stdout"]}


def default_network_mode() -> str:
    profile = host_profile.load_host_profile()
    return profile.get("recommended_network", "bridge")


def vm_boot_order_options() -> list[dict[str, str]]:
    return [
        {"value": "auto", "label": "Auto — по источнику VM"},
        {"value": "disk", "label": "Сначала диск"},
        {"value": "cdrom_disk", "label": "Сначала ISO/CD-ROM, потом диск"},
        {"value": "disk_cdrom", "label": "Сначала диск, потом ISO/CD-ROM"},
        {"value": "network_disk", "label": "Сначала сеть/PXE, потом диск"},
    ]


def normalize_boot_order(value: str, source_type: str) -> str:
    value = (value or "auto").strip()
    if value == "auto":
        return "cdrom_disk" if source_type == "iso" else "disk"
    if value in ("disk", "cdrom_disk", "disk_cdrom", "network_disk"):
        return value
    return "cdrom_disk" if source_type == "iso" else "disk"


def virt_boot_arg(boot_order: str, is_arm: bool) -> str:
    mapping = {
        "disk": "hd",
        "cdrom_disk": "cdrom,hd",
        "disk_cdrom": "hd,cdrom",
        "network_disk": "network,hd",
    }
    value = mapping.get(boot_order, "hd")
    if is_arm:
        return "uefi," + value
    return value


def vm_form_context(request: Request, error: str | None = None, form: dict[str, Any] | None = None, status_code: int = 200):
    profile = host_profile.load_host_profile()
    default_mode = profile.get("recommended_network", "nat")
    return templates.TemplateResponse("vm_create.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "isos": list_iso_files(), "disk_images": list_disk_image_files(), "arch_options": vm_arch_options(), "boot_options": vm_boot_order_options(), "error": error, "profile": profile, "form": form or {"memory": 4096, "vcpus": 2, "disk_size": 40, "source_type": "iso", "guest_arch": "auto", "boot_order": "auto", "network_mode": default_mode, "bridge": DEFAULT_BRIDGE}}, status_code=status_code)


# Virtuality noVNC console patch
async def proxy_vnc_to_websocket(reader: asyncio.StreamReader, websocket: WebSocket) -> None:
    while True:
        data = await reader.read(65536)
        if not data:
            break
        try:
            await websocket.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            break


async def proxy_websocket_to_vnc(websocket: WebSocket, writer: asyncio.StreamWriter) -> None:
    while True:
        try:
            message = await websocket.receive()
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            break
        if message.get("type") == "websocket.disconnect":
            break
        if message.get("bytes") is not None:
            writer.write(message["bytes"])
        elif message.get("text") is not None:
            writer.write(message["text"].encode())
        try:
            await writer.drain()
        except (RuntimeError, ConnectionError, BrokenPipeError):
            break

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME, "error": None, "configured": is_configured(), "auth_user": AUTH_USER})


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if not is_configured():
        return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME, "error": "Панель ещё не настроена. Запусти установщик веб-панели повторно.", "configured": False, "auth_user": AUTH_USER}, status_code=500)
    key = client_key(request)
    retry_after = login_throttle.retry_after(key)
    if retry_after:
        return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME, "error": f"Слишком много неудачных попыток входа. Повтори через {retry_after} сек.", "configured": True, "auth_user": AUTH_USER}, status_code=429, headers={"Retry-After": str(retry_after)})
    if not verify_linux_password(username, password):
        login_throttle.record_failure(key)
        return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME, "error": "Неверный логин или пароль Linux-пользователя", "configured": True, "auth_user": AUTH_USER}, status_code=401)
    login_throttle.record_success(key)
    token = serializer.dumps({"user": AUTH_USER})
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("virtuality_session", token, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=SESSION_MAX_AGE)
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("virtuality_session")
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    services = {"libvirtd": service_state("libvirtd.service"), "virtlogd": service_state("virtlogd.service"), "cockpit": service_state("cockpit.socket"), "dashboard": service_state("virtuality-console-dashboard.service"), "web": service_state("virtuality-web.service")}
    service_rows = [{"key": key, "name": presenters.SERVICE_LABELS.get(key, key), "state": state, **presenters.service_state(state)} for key, state in services.items()]
    return templates.TemplateResponse("dashboard.html", {"request": request, "app_name": APP_NAME, "system": system_summary(), "services": services, "service_rows": service_rows, "services_ok": all(row["tone"] == "success" for row in service_rows if row["key"] in ("libvirtd", "web")), "vms": parse_virsh_list(), "pools": parse_pool_list(), "network": network_summary(), "user": AUTH_USER, "profile": host_profile.load_host_profile(), "operations": list_operations(5), "operation_css": operation_css, "host": presenters.host_stats(STORAGE_DIR), "greeting": presenters.greeting()})


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    return templates.TemplateResponse("help.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "host_ip": system_summary()["ip"]})


@app.get("/host", response_class=HTMLResponse)
def host_page(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    profile = host_profile.load_host_profile()
    return templates.TemplateResponse("host.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "profile": profile, "profile_json": json.dumps(profile, ensure_ascii=False, indent=2)})


@app.post("/host/refresh")
def host_refresh(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    profile = host_profile.detect_host_profile()
    host_profile.save_host_profile(profile)
    return RedirectResponse(url="/host", status_code=303)


@app.get("/iso", response_class=HTMLResponse)
def iso_page(request: Request, error: str | None = None):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    return templates.TemplateResponse("iso.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "isos": list_iso_files(), "error": error})


@app.post("/iso/upload", response_class=HTMLResponse)
def iso_upload(request: Request, iso_file: UploadFile = File(...)):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    safe_name = safe_iso_filename(iso_file.filename or "")
    if not safe_name:
        return templates.TemplateResponse("iso.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "isos": list_iso_files(), "error": "Можно загружать только .iso файлы с безопасным именем."}, status_code=400)
    ISO_DIR.mkdir(parents=True, exist_ok=True)
    target = ISO_DIR / safe_name
    if target.exists():
        return templates.TemplateResponse("iso.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "isos": list_iso_files(), "error": f"ISO уже существует: {safe_name}"}, status_code=400)
    tmp_target = ISO_DIR / f".{safe_name}.uploading"
    try:
        with tmp_target.open("wb") as out:
            shutil.copyfileobj(iso_file.file, out)
        tmp_target.rename(target)
        refresh_iso_pool()
    except Exception as exc:
        tmp_target.unlink(missing_ok=True)
        return templates.TemplateResponse("iso.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "isos": list_iso_files(), "error": f"Ошибка загрузки ISO: {exc}. Проверь свободное место, права на /var/lib/virtuality/iso и временный каталог /var/lib/virtuality/tmp."}, status_code=500)
    return RedirectResponse(url="/iso", status_code=303)


@app.post("/iso/{name}/delete")
def iso_delete(request: Request, name: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    path = iso_path_by_name(name)
    if path and path.exists() and path.is_file():
        path.unlink()
        refresh_iso_pool()
    return RedirectResponse(url="/iso", status_code=303)


@app.post("/iso/refresh")
def iso_refresh(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    refresh_iso_pool()
    return RedirectResponse(url="/iso", status_code=303)


@app.get("/disk-images", response_class=HTMLResponse)
def disk_images_page(request: Request, error: str | None = None):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": error})


@app.post("/disk-images/upload", response_class=HTMLResponse)
def disk_image_upload(request: Request, image_file: UploadFile = File(...)):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    safe_name = safe_disk_upload_filename(image_file.filename or "")
    if not safe_name:
        return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": "Можно загружать только .img, .raw, .qcow2, .img.xz, .zip, .tar.gz или .tgz файлы."}, status_code=400)
    DISK_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    tmp_target = DISK_IMAGES_DIR / f".{safe_name}.uploading"
    saved_paths: list[Path] = []
    try:
        with tmp_target.open("wb") as out:
            shutil.copyfileobj(image_file.file, out, length=1024 * 1024)
        if disk_upload_is_archive(safe_name):
            saved_paths = extract_disk_archive(tmp_target)
            tmp_target.unlink(missing_ok=True)
        elif disk_upload_is_xz_image(safe_name):
            saved_paths = [extract_xz_disk_image(tmp_target, safe_name)]
            tmp_target.unlink(missing_ok=True)
        else:
            target = unique_disk_image_path(safe_name)
            tmp_target.rename(target)
            saved_paths = [target]
    except Exception as exc:
        tmp_target.unlink(missing_ok=True)
        for path in saved_paths:
            path.unlink(missing_ok=True)
        return templates.TemplateResponse("disk_images.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "images": list_disk_image_files(), "error": f"Ошибка загрузки/распаковки образа: {exc}"}, status_code=500)

    operations = []
    for path in saved_paths:
        operation = start_disk_convert_operation(path)
        if operation:
            operations.append(operation)

    payload = {
        "ok": True,
        "mode": "converting" if operations else "ready",
        "operation_id": operations[0]["id"] if operations else None,
        "operation_ids": [op["id"] for op in operations],
        "files": [path.name for path in saved_paths],
        "message": f"Загружено файлов: {len(saved_paths)}. Конвертаций запущено: {len(operations)}.",
    }
    return disk_upload_response(request, payload)


@app.post("/disk-images/{name}/delete")
def disk_image_delete(request: Request, name: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    path = disk_image_path_by_name(name)
    if path and path.exists() and path.is_file():
        path.unlink()
    return RedirectResponse(url="/disk-images", status_code=303)


def safe_network_template(request: Request, error: str | None = None, status_code: int = 200, diagnostics: dict[str, Any] | None = None):
    try:
        ctx = network_core.network_context()
    except Exception as exc:
        ctx = {
            "nat": {
                "name": network_core.NETWORK_NAME,
                "bridge": network_core.NAT_BRIDGE,
                "subnet": network_core.NAT_SUBNET,
                "gateway": network_core.NAT_GATEWAY,
                "dhcp": f"{network_core.DHCP_START} - {network_core.DHCP_END}",
                "exists": False,
                "info": str(exc),
                "leases": "",
            },
            "networks": [],
            "forwards": [],
            "external_interface": "unknown",
            "ip_forward": "unknown",
            "nft_rules": "",
        }
        error = error or f"Ошибка чтения сетевого состояния: {exc}"
    return templates.TemplateResponse("network.html", {
        "request": request,
        "app_name": APP_NAME,
        "user": AUTH_USER,
        "vms": parse_virsh_list(),
        "ctx": ctx,
        "error": error,
        "diagnostics": diagnostics,
    }, status_code=status_code)


@app.get("/network", response_class=HTMLResponse)
def network_page(request: Request, error: str | None = None):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    return safe_network_template(request, error=error)


@app.post("/network/vm-ip/save")
def network_vm_ip_save(request: Request, vm_name: str = Form(...), manual_ip: str = Form("")):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    if not valid_vm_name(vm_name):
        return RedirectResponse(url="/network", status_code=303)
    manual_ip = (manual_ip or "").strip()
    if manual_ip and not re.fullmatch(r"(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}", manual_ip):
        return templates.TemplateResponse("network.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vms": parse_virsh_list(), "ctx": network_core.network_context(), "error": "Некорректный ручной IP VM"}, status_code=400)
    path = Path("/var/lib/virtuality/network/vm_ips.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    if manual_ip:
        data[vm_name] = manual_ip
    else:
        data.pop(vm_name, None)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return RedirectResponse(url="/network", status_code=303)


@app.post("/network/nat/setup")
def network_nat_setup(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        network_core.create_nat_network()
        network_core.apply_port_forwards()
    except NetworkError as exc:
        return safe_network_template(request, error=str(exc), status_code=500)
    except Exception as exc:
        return safe_network_template(request, error=f"Внутренняя ошибка настройки NAT: {exc}", status_code=500)
    return RedirectResponse(url="/network", status_code=303)


@app.post("/network/forward/add")
def network_forward_add(request: Request, vm_name: str = Form(...), guest_ip: str = Form(...), external_port: str = Form(...), guest_port: str = Form(...), protocol: str = Form("tcp"), note: str = Form("")):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        network_core.add_port_forward(vm_name, guest_ip, external_port, guest_port, protocol, note)
    except NetworkError as exc:
        return safe_network_template(request, error=str(exc), status_code=400)
    return RedirectResponse(url="/network", status_code=303)


@app.post("/network/forward/{forward_id}/delete")
def network_forward_delete(request: Request, forward_id: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        network_core.delete_port_forward(forward_id)
    except NetworkError:
        pass
    return RedirectResponse(url="/network", status_code=303)


@app.post("/network/apply")
def network_apply(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        network_core.apply_port_forwards()
    except NetworkError as exc:
        return safe_network_template(request, error=str(exc), status_code=500)
    return RedirectResponse(url="/network", status_code=303)


@app.post("/network/diagnose", response_class=HTMLResponse)
def network_diagnose(request: Request, vm_name: str = Form(...), external_port: int = Form(...), guest_port: int = Form(...), protocol: str = Form("tcp")):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    diagnostics = None
    error = None
    try:
        diagnostics = network_core.diagnose_public_access(vm_name, external_port, guest_port, protocol)
    except NetworkError as exc:
        error = str(exc)
    return templates.TemplateResponse("network.html", {
        "request": request,
        "app_name": APP_NAME,
        "user": AUTH_USER,
        "vms": parse_virsh_list(),
        "ctx": network_core.network_context(),
        "error": error,
        "diagnostics": diagnostics,
    }, status_code=400 if error else 200)


@app.get("/update", response_class=HTMLResponse)
def update_page(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    error = None
    try:
        info = update_core.check_updates(fetch=False)
    except Exception as exc:
        error = str(exc)
        info = {
            "ok": False,
            "source_dir": str(update_core.SOURCE_DIR),
            "remote": update_core.REMOTE,
            "branch": update_core.DEFAULT_BRANCH,
            "fetch_ok": False,
            "fetch_error": str(exc),
            "current_commit": "",
            "latest_commit": "",
            "current_version": "unknown",
            "latest_version": "unknown",
            "has_update": False,
            "missing_versions": [],
            "commits": [],
            "checked_at": utc_now(),
            "state": update_core.state(),
            "log_tail": update_core.update_log_tail(),
        }
    current = str(info.get("current_version", ""))
    current_release = next((item for item in update_core.load_manifest().get("versions", []) if str(item.get("version")) == current), None)
    return templates.TemplateResponse("update.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "info": info, "error": error, "current_release": current_release})


@app.get("/update/status")
def update_status(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    return JSONResponse({
        "ok": True,
        "state": update_core.state(),
        "log_tail": update_core.update_log_tail(260),
        "checked_at": utc_now(),
    })


@app.post("/update/check")
def update_check(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        update_core.check_updates(fetch=True)
    except Exception:
        pass
    return RedirectResponse(url="/update", status_code=303)


@app.post("/update/apply")
def update_apply(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        update_core.start_update()
    except Exception:
        pass
    return RedirectResponse(url="/update", status_code=303)


@app.get("/operations", response_class=HTMLResponse)
def operations_page(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    return templates.TemplateResponse("operations.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "operations": list_operations(50), "operation_css": operation_css})


@app.get("/operations/{operation_id}", response_class=HTMLResponse)
def operation_detail_page(request: Request, operation_id: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    operation = read_operation(operation_id)
    if not operation:
        return RedirectResponse(url="/operations", status_code=303)
    return templates.TemplateResponse("operation_detail.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "operation": operation, "operation_css": operation_css})


@app.get("/api/operations")
def api_operations(request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    return {"ok": True, "operations": list_operations(25)}


@app.get("/api/operations/{operation_id}")
def api_operation(request: Request, operation_id: str):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    operation = read_operation(operation_id)
    if not operation:
        return JSONResponse({"ok": False, "error": "Operation not found"}, status_code=404)
    return {"ok": True, "operation": operation}


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, source: str = "web", lines: int = 220):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    selected = read_log_source(source, lines)
    return templates.TemplateResponse("logs.html", {
        "request": request,
        "app_name": APP_NAME,
        "user": AUTH_USER,
        "sources": LOG_SOURCES,
        "selected": selected,
    })


@app.get("/api/logs")
def api_logs(request: Request, source: str = "web", lines: int = 220):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    return {"ok": True, "log": read_log_source(source, lines)}


@app.get("/vm/create", response_class=HTMLResponse)
def vm_create_page(request: Request, iso: str = "", image: str = ""):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    if not iso and not image:
        return vm_form_context(request)
    profile = host_profile.load_host_profile()
    form = {"memory": 4096, "vcpus": 2, "disk_size": 40, "source_type": "disk_image" if image else "iso", "guest_arch": "auto", "boot_order": "auto", "network_mode": profile.get("recommended_network", "nat"), "bridge": DEFAULT_BRIDGE}
    if iso:
        form["iso_path"] = str(ISO_DIR / Path(iso).name)
    if image:
        form["disk_image_path"] = str(DISK_IMAGES_DIR / Path(image).name)
    return vm_form_context(request, form=form)


@app.post("/vm/create", response_class=HTMLResponse)
def vm_create_submit(request: Request, name: str = Form(...), memory: int = Form(...), vcpus: int = Form(...), disk_size: int = Form(20), iso_path: str = Form(""), disk_image_path: str = Form(""), source_type: str = Form("iso"), guest_arch: str = Form("auto"), boot_order: str = Form("auto"), network_mode: str = Form("nat"), bridge: str = Form(DEFAULT_BRIDGE)):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    form = {"name": name, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "iso_path": iso_path, "disk_image_path": disk_image_path, "source_type": source_type, "guest_arch": guest_arch, "boot_order": boot_order, "network_mode": network_mode, "bridge": bridge}
    error = None
    if not valid_vm_name(name):
        error = "Имя VM может содержать латиницу, цифры, точку, дефис и подчёркивание. Длина 2–63 символа."
    elif vm_exists(name):
        error = f"VM с именем {name} уже существует."
    elif memory < 512 or memory > 262144:
        error = "RAM должна быть от 512 MB до 262144 MB."
    elif vcpus < 1 or vcpus > 128:
        error = "CPU должен быть от 1 до 128 vCPU."
    elif disk_size < 4 or disk_size > 4096:
        error = "Диск должен быть от 4 GB до 4096 GB."
    elif network_mode not in ("nat", "bridge"):
        error = "Некорректный режим сети."
    elif network_mode == "bridge" and (not bridge or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", bridge)):
        error = "Некорректное имя bridge."
    elif source_type not in ("iso", "disk_image"):
        error = "Некорректный источник VM."
    elif guest_arch not in ("auto", "x86_64", "aarch64", "generic"):
        error = "Некорректная архитектура VM."
    elif boot_order not in ("auto", "disk", "cdrom_disk", "disk_cdrom", "network_disk"):
        error = "Некорректный порядок загрузки VM."
    elif network_mode == "bridge" and not bridge_exists(bridge):
        error = f"Bridge {bridge} не найден на сервере. Для VPS выбери режим NAT Router — virtuality-nat, либо сначала создай bridge {bridge}."
    else:
        if source_type == "iso":
            iso = Path(iso_path).resolve()
            if ISO_DIR.resolve() not in iso.parents or iso.suffix.lower() != ".iso" or not iso.exists():
                error = "ISO должен быть существующим .iso файлом из /var/lib/virtuality/iso."
        else:
            disk_image = Path(disk_image_path).resolve()
            if DISK_IMAGES_DIR.resolve() not in disk_image.parents or disk_image.suffix.lower() not in (".img", ".raw", ".qcow2") or not disk_image.exists():
                error = "Образ диска должен быть существующим .img, .raw или .qcow2 файлом из /var/lib/virtuality/disk-images."
    if error:
        return vm_form_context(request, error=error, form=form, status_code=400)

    if network_mode == "nat":
        try:
            network_core.create_nat_network()
        except NetworkError as exc:
            return vm_form_context(request, error=f"NAT-сеть не готова: {exc}", form=form, status_code=500)

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    disk_path = IMAGES_DIR / f"{name}.qcow2"
    if disk_path.exists():
        return vm_form_context(request, error=f"Диск уже существует: {disk_path}", form=form, status_code=400)

    profile = host_profile.load_host_profile()
    selected_arch = normalize_guest_arch(guest_arch, profile)
    selected_boot_order = normalize_boot_order(boot_order, source_type)
    host_arch = str(profile.get("arch") or platform.machine() or "")
    is_arm = selected_arch == "aarch64"
    # ARM64 guest on x86 host cannot use KVM. It must use QEMU emulation.
    virt_type = "qemu" if (is_arm and host_arch not in ("aarch64", "arm64")) else ("kvm" if profile.get("kvm_device") else "qemu")
    network_arg = f"network={network_core.NETWORK_NAME},model=virtio" if network_mode == "nat" else f"bridge={bridge},model=virtio"
    cmd = ["virt-install", "--name", name, "--memory", str(memory), "--vcpus", str(vcpus), "--virt-type", virt_type]
    if selected_arch == "x86_64":
        cmd += ["--arch", "x86_64"]
    elif is_arm:
        cmd += ["--arch", "aarch64", "--machine", "virt", "--cpu", "host" if virt_type == "kvm" else "cortex-a57"]

    cmd += ["--boot", virt_boot_arg(selected_boot_order, is_arm)]

    if source_type == "disk_image":
        source_disk = Path(disk_image_path).resolve()
        source_format = disk_image_format(source_disk)
        convert_cmd = f"qemu-img convert -p -f {source_format} -O qcow2 {source_disk} {disk_path}"
        virt_cmd = " ".join(cmd + ["--import", "--disk", f"path={disk_path},format=qcow2,bus=virtio", "--os-variant", "generic", "--network", network_arg, "--graphics", "vnc,listen=0.0.0.0", "--noautoconsole"])
        cmd = ["bash", "-lc", f"set -euo pipefail; {convert_cmd}; {virt_cmd}"]
    else:
        cmd += ["--disk", f"path={disk_path},size={disk_size},format=qcow2,bus=virtio", "--cdrom", iso_path, "--os-variant", "generic", "--network", network_arg, "--graphics", "vnc,listen=0.0.0.0", "--noautoconsole"]

    operation_id = str(uuid.uuid4())
    operation = {"id": operation_id, "type": "vm_create", "title": f"Создание VM {name}", "status": "queued", "progress": 0, "message": "Операция поставлена в очередь", "created_at": utc_now(), "updated_at": utc_now(), "created_by": AUTH_USER, "vm_name": name, "disk_path": str(disk_path), "iso_path": iso_path, "host_profile": profile.get("profile"), "guest_arch": selected_arch, "boot_order": selected_boot_order, "network_mode": network_mode, "network": network_arg, "bridge": bridge, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "cmd": " ".join(cmd)}
    start_background_operation(operation, cmd)
    return RedirectResponse(url=f"/operations/{operation_id}", status_code=303)


@app.get("/vm/{name}/console", response_class=HTMLResponse)
def vm_console_page(request: Request, name: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    if not valid_vm_name(name) or not vm_exists(name):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("console.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vm": vm_details(name), "console": console_info(name)})


@app.websocket("/console/ws/{token}")
async def console_websocket(websocket: WebSocket, token: str):
    if user_from_session_token(websocket.cookies.get("virtuality_session")) != AUTH_USER:
        await websocket.close(code=1008)
        return
    try:
        payload = console_serializer.loads(token)
        vm_name = payload.get("vm")
        target_port = int(payload.get("port"))
    except Exception:
        await websocket.close(code=1008)
        return
    if not valid_vm_name(vm_name) or not vm_exists(vm_name) or target_port < 5900 or target_port > 5999:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", target_port)
    except Exception:
        await websocket.close(code=1011)
        return
    tasks = [
        asyncio.create_task(proxy_vnc_to_websocket(reader, websocket)),
        asyncio.create_task(proxy_websocket_to_vnc(websocket, writer)),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*done, return_exceptions=True)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


@app.get("/vm/{name}", response_class=HTMLResponse)
def vm_detail_page(request: Request, name: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    if not valid_vm_name(name) or not vm_exists(name):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("vm_detail.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vm": vm_details(name), "host_ip": system_summary()["ip"], "boot_options": vm_boot_order_options(), "current_boot_order": current_vm_boot_order(name), "boot_message": request.query_params.get("boot_message", ""), "boot_error": request.query_params.get("boot_error", ""), "resource_settings": vm_resource_settings(name), "resource_message": request.query_params.get("resource_message", ""), "resource_error": request.query_params.get("resource_error", ""), "isos": list_iso_files(), "current_iso": current_vm_iso(name), "iso_message": request.query_params.get("iso_message", ""), "iso_error": request.query_params.get("iso_error", "")})


@app.post("/vm/{name}/resources")
def vm_resources_apply(request: Request, name: str, memory_mb: int = Form(...), vcpus: int = Form(...), guest_arch: str = Form("keep")):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    ok, message = apply_vm_resources(name, memory_mb, vcpus, guest_arch)
    if ok:
        return redirect_with_message(f"/vm/{name}", "resource_message", message)
    return redirect_with_message(f"/vm/{name}", "resource_error", message)


@app.post("/vm/{name}/boot-order")
def vm_boot_order_apply(request: Request, name: str, boot_order: str = Form("auto")):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    ok, message = apply_vm_boot_order(name, boot_order)
    if ok:
        return redirect_with_message(f"/vm/{name}", "boot_message", message)
    return redirect_with_message(f"/vm/{name}", "boot_error", message)


@app.post("/vm/{name}/iso/mount")
def vm_iso_mount_apply(request: Request, name: str, iso_path: str = Form(...)):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    ok, message = mount_vm_iso(name, iso_path)
    if ok:
        return redirect_with_message(f"/vm/{name}", "iso_message", message)
    return redirect_with_message(f"/vm/{name}", "iso_error", message)


@app.post("/vm/{name}/iso/unmount")
def vm_iso_unmount_apply(request: Request, name: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    ok, message = detach_vm_iso(name)
    if ok:
        return redirect_with_message(f"/vm/{name}", "iso_message", message)
    return redirect_with_message(f"/vm/{name}", "iso_error", message)


@app.post("/vm/{name}/{action}")
def vm_action(request: Request, name: str, action: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    if not valid_vm_name(name):
        return JSONResponse({"ok": False, "error": "Invalid VM name"}, status_code=400)
    allowed = {"start": ["virsh", "start", name], "shutdown": ["virsh", "shutdown", name], "reboot": ["virsh", "reboot", name], "destroy": ["virsh", "destroy", name], "autostart": ["virsh", "autostart", name], "autostart-disable": ["virsh", "autostart", "--disable", name]}
    if action == "delete":
        run_cmd(["virsh", "destroy", name], timeout=20)
        run_cmd(["virsh", "undefine", name, "--remove-all-storage"], timeout=60)
        return RedirectResponse(url="/", status_code=303)
    if action not in allowed:
        return JSONResponse({"ok": False, "error": "Unsupported action"}, status_code=400)
    run_cmd(allowed[action], timeout=30)
    return RedirectResponse(url=f"/vm/{name}", status_code=303)


@app.get("/live/status")
def live_status(request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    vms = []
    for vm in parse_virsh_list():
        name = vm.get("name", "")
        state = vm.get("state", "unknown")
        try:
            ip = vm_ip(name) if name else "—"
        except Exception:
            ip = "—"
        css = "ok" if "running" in state else "err" if "shut" in state else "warn"
        vms.append({"id": vm.get("id", "-"), "name": name, "state": state, "state_css": css, "ip": ip if ip and ip != "not available" else "—", "autostart_enabled": vm.get("autostart_enabled", False), "autostart_label": vm.get("autostart_label", "unknown"), "autostart_css": vm.get("autostart_css", "warn")})
    return JSONResponse({"ok": True, "generated_at": utc_now(), "vms": vms, "services": {"libvirtd": service_state("libvirtd.service"), "virtlogd": service_state("virtlogd.service"), "cockpit": service_state("cockpit.socket"), "web": service_state("virtuality-web.service")}, "operations": list_operations(5)})


@app.get("/live/operations")
def live_operations(request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    return JSONResponse({"ok": True, "generated_at": utc_now(), "operations": list_operations(25)})


@app.get("/healthz")
def healthz():
    return {"ok": True, "app": APP_NAME, "version": APP_VERSION}


@app.get("/api/health")
def api_health(request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    return {"system": system_summary(), "host_profile": host_profile.load_host_profile(), "services": {"libvirtd": service_state("libvirtd.service"), "virtlogd": service_state("virtlogd.service"), "cockpit": service_state("cockpit.socket"), "dashboard": service_state("virtuality-console-dashboard.service"), "web": service_state("virtuality-web.service")}, "vms": parse_virsh_list(), "pools": parse_pool_list(), "network": network_summary(), "virtuality_nat": network_core.network_context()}


import features.backups as backups  # noqa: E402
app.include_router(backups.router)
