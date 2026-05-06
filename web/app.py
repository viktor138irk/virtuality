#!/usr/bin/env python3
import crypt
import json
import os
import re
import secrets
import shutil
import spwd
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

import host_profile
import network_core
from network_core import NetworkError

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = "Virtuality"
ENV_FILE = BASE_DIR / ".env"
ISO_DIR = Path("/var/lib/virtuality/iso")
IMAGES_DIR = Path("/var/lib/virtuality/images")
OPERATIONS_DIR = Path("/var/log/virtuality/operations")
DEFAULT_BRIDGE = "br0"

app = FastAPI(title="Virtuality Panel")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
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
AUTH_USER = CONFIG.get("VIRTUALITY_AUTH_USER", os.environ.get("VIRTUALITY_AUTH_USER", "viktor"))
SESSION_SECRET = CONFIG.get("VIRTUALITY_SESSION_SECRET", os.environ.get("VIRTUALITY_SESSION_SECRET", "dev-secret-change-me"))
serializer = URLSafeSerializer(SESSION_SECRET, salt="virtuality-session")


def is_configured() -> bool:
    return bool(AUTH_USER and SESSION_SECRET != "dev-secret-change-me")


def verify_linux_password(username: str, password: str) -> bool:
    if not username or not password or username != AUTH_USER:
        return False
    try:
        shadow = spwd.getspnam(username)
    except (PermissionError, KeyError):
        return False
    stored_hash = shadow.sp_pwdp
    if stored_hash in ("!", "*", "!!", ""):
        return False
    return secrets.compare_digest(crypt.crypt(password, stored_hash), stored_hash)


def get_current_user(request: Request) -> str | None:
    token = request.cookies.get("virtuality_session")
    if not token:
        return None
    try:
        data = serializer.loads(token)
    except BadSignature:
        return None
    return AUTH_USER if data.get("user") == AUTH_USER else None


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


def parse_virsh_list() -> list[dict[str, str]]:
    result = run_cmd(["virsh", "list", "--all"])
    rows = []
    if not result["ok"]:
        return rows
    for line in result["stdout"].splitlines()[2:]:
        parts = line.strip().split(None, 2)
        if len(parts) == 3:
            rows.append({"id": parts[0], "name": parts[1], "state": parts[2]})
        elif len(parts) == 2:
            rows.append({"id": "-", "name": parts[0], "state": parts[1]})
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


def safe_iso_filename(filename: str) -> str | None:
    name = Path(filename or "").name.strip().replace(" ", "-")
    name = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
    if not name.lower().endswith(".iso"):
        return None
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,180}\.iso", name):
        return None
    return name


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


def vm_details(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "dominfo": run_cmd(["virsh", "dominfo", name], timeout=10)["stdout"],
        "vnc": run_cmd(["virsh", "vncdisplay", name], timeout=8)["stdout"] or "not available",
        "ip": vm_ip(name),
        "disks": run_cmd(["virsh", "domblklist", name, "--details"], timeout=10)["stdout"],
        "interfaces": run_cmd(["virsh", "domiflist", name], timeout=10)["stdout"],
        "autostart": run_cmd(["virsh", "dominfo", name], timeout=10)["stdout"],
    }


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


def vm_form_context(request: Request, error: str | None = None, form: dict[str, Any] | None = None, status_code: int = 200):
    profile = host_profile.load_host_profile()
    default_mode = profile.get("recommended_network", "nat")
    return templates.TemplateResponse("vm_create.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "isos": list_iso_files(), "error": error, "profile": profile, "form": form or {"memory": 2048, "vcpus": 2, "disk_size": 20, "network_mode": default_mode, "bridge": DEFAULT_BRIDGE}}, status_code=status_code)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME, "error": None, "configured": is_configured(), "auth_user": AUTH_USER})


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if not is_configured():
        return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME, "error": "Панель ещё не настроена. Запусти установщик веб-панели повторно.", "configured": False, "auth_user": AUTH_USER}, status_code=500)
    if not verify_linux_password(username, password):
        return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME, "error": "Неверный логин или пароль Linux-пользователя", "configured": True, "auth_user": AUTH_USER}, status_code=401)
    token = serializer.dumps({"user": AUTH_USER})
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("virtuality_session", token, httponly=True, samesite="lax", max_age=60 * 60 * 12)
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
    return templates.TemplateResponse("dashboard.html", {"request": request, "app_name": APP_NAME, "system": system_summary(), "services": services, "vms": parse_virsh_list(), "pools": parse_pool_list(), "network": network_summary(), "user": AUTH_USER, "profile": host_profile.load_host_profile(), "operations": list_operations(5), "operation_css": operation_css})


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
        return templates.TemplateResponse("iso.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "isos": list_iso_files(), "error": f"Ошибка загрузки ISO: {exc}"}, status_code=500)
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


@app.get("/network", response_class=HTMLResponse)
def network_page(request: Request, error: str | None = None):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    return templates.TemplateResponse("network.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vms": parse_virsh_list(), "ctx": network_core.network_context(), "error": error})


@app.post("/network/nat/setup")
def network_nat_setup(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        network_core.create_nat_network()
        network_core.apply_port_forwards()
    except NetworkError as exc:
        return templates.TemplateResponse("network.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vms": parse_virsh_list(), "ctx": network_core.network_context(), "error": str(exc)}, status_code=500)
    return RedirectResponse(url="/network", status_code=303)


@app.post("/network/forward/add")
def network_forward_add(request: Request, vm_name: str = Form(...), guest_ip: str = Form(...), external_port: int = Form(...), guest_port: int = Form(...), protocol: str = Form("tcp"), note: str = Form("")):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    try:
        network_core.add_port_forward(vm_name, guest_ip, external_port, guest_port, protocol, note)
    except NetworkError as exc:
        return templates.TemplateResponse("network.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vms": parse_virsh_list(), "ctx": network_core.network_context(), "error": str(exc)}, status_code=400)
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
        return templates.TemplateResponse("network.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vms": parse_virsh_list(), "ctx": network_core.network_context(), "error": str(exc)}, status_code=500)
    return RedirectResponse(url="/network", status_code=303)


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


@app.get("/vm/create", response_class=HTMLResponse)
def vm_create_page(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    return vm_form_context(request)


@app.post("/vm/create", response_class=HTMLResponse)
def vm_create_submit(request: Request, name: str = Form(...), memory: int = Form(...), vcpus: int = Form(...), disk_size: int = Form(...), iso_path: str = Form(...), network_mode: str = Form("nat"), bridge: str = Form(DEFAULT_BRIDGE)):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    form = {"name": name, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "iso_path": iso_path, "network_mode": network_mode, "bridge": bridge}
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
    else:
        iso = Path(iso_path).resolve()
        if ISO_DIR.resolve() not in iso.parents or iso.suffix.lower() != ".iso" or not iso.exists():
            error = "ISO должен быть существующим .iso файлом из /var/lib/virtuality/iso."
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
    is_arm = profile.get("recommended_guest_arch") == "aarch64"
    network_arg = f"network={network_core.NETWORK_NAME},model=virtio" if network_mode == "nat" else f"bridge={bridge},model=virtio"
    cmd = ["virt-install", "--name", name, "--memory", str(memory), "--vcpus", str(vcpus)]
    if is_arm:
        cmd += ["--arch", "aarch64", "--machine", "virt", "--cpu", "host", "--virt-type", "kvm", "--boot", "uefi"]
    cmd += ["--disk", f"path={disk_path},size={disk_size},format=qcow2,bus=virtio", "--cdrom", iso_path, "--os-variant", "generic", "--network", network_arg, "--graphics", "vnc,listen=0.0.0.0", "--noautoconsole"]

    operation_id = str(uuid.uuid4())
    operation = {"id": operation_id, "type": "vm_create", "title": f"Создание VM {name}", "status": "queued", "progress": 0, "message": "Операция поставлена в очередь", "created_at": utc_now(), "updated_at": utc_now(), "created_by": AUTH_USER, "vm_name": name, "disk_path": str(disk_path), "iso_path": iso_path, "host_profile": profile.get("profile"), "guest_arch": profile.get("recommended_guest_arch"), "network_mode": network_mode, "network": network_arg, "bridge": bridge, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "cmd": " ".join(cmd)}
    start_background_operation(operation, cmd)
    return RedirectResponse(url=f"/operations/{operation_id}", status_code=303)


@app.get("/vm/{name}", response_class=HTMLResponse)
def vm_detail_page(request: Request, name: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    if not valid_vm_name(name) or not vm_exists(name):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("vm_detail.html", {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "vm": vm_details(name), "host_ip": system_summary()["ip"]})


@app.post("/vm/{name}/{action}")
def vm_action(request: Request, name: str, action: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    allowed = {"start": ["virsh", "start", name], "shutdown": ["virsh", "shutdown", name], "reboot": ["virsh", "reboot", name], "destroy": ["virsh", "destroy", name], "autostart": ["virsh", "autostart", name], "autostart-disable": ["virsh", "autostart", "--disable", name]}
    if action == "delete":
        run_cmd(["virsh", "destroy", name], timeout=20)
        run_cmd(["virsh", "undefine", name, "--remove-all-storage"], timeout=60)
        return RedirectResponse(url="/", status_code=303)
    if action not in allowed:
        return JSONResponse({"ok": False, "error": "Unsupported action"}, status_code=400)
    run_cmd(allowed[action], timeout=30)
    return RedirectResponse(url=f"/vm/{name}", status_code=303)


@app.get("/api/health")
def api_health(request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    return {"system": system_summary(), "host_profile": host_profile.load_host_profile(), "services": {"libvirtd": service_state("libvirtd.service"), "virtlogd": service_state("virtlogd.service"), "cockpit": service_state("cockpit.socket"), "dashboard": service_state("virtuality-console-dashboard.service"), "web": service_state("virtuality-web.service")}, "vms": parse_virsh_list(), "pools": parse_pool_list(), "network": network_summary(), "virtuality_nat": network_core.network_context()}
