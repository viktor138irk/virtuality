#!/usr/bin/env python3
import crypt
import os
import re
import secrets
import spwd
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = "Virtuality"
ENV_FILE = BASE_DIR / ".env"
ISO_DIR = Path("/var/lib/virtuality/iso")
IMAGES_DIR = Path("/var/lib/virtuality/images")
DEFAULT_BRIDGE = "br0"

app = FastAPI(title="Virtuality Panel")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def load_env() -> dict[str, str]:
    data: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw_line in ENV_FILE.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
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
    if not username or not password:
        return False
    if username != AUTH_USER:
        return False
    try:
        shadow = spwd.getspnam(username)
    except PermissionError:
        return False
    except KeyError:
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
    if data.get("user") == AUTH_USER:
        return AUTH_USER
    return None


def require_auth(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return None


def run_cmd(cmd: list[str], timeout: int = 12) -> dict[str, Any]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": result.returncode == 0,
            "code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "cmd": " ".join(cmd),
        }
    except Exception as exc:
        return {
            "ok": False,
            "code": -1,
            "stdout": "",
            "stderr": str(exc),
            "cmd": " ".join(cmd),
        }


def parse_virsh_list() -> list[dict[str, str]]:
    result = run_cmd(["virsh", "list", "--all"])
    if not result["ok"]:
        return []
    rows = []
    for line in result["stdout"].splitlines()[2:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) == 3:
            rows.append({"id": parts[0], "name": parts[1], "state": parts[2]})
        elif len(parts) == 2:
            rows.append({"id": "-", "name": parts[0], "state": parts[1]})
    return rows


def parse_pool_list() -> list[dict[str, str]]:
    result = run_cmd(["virsh", "pool-list", "--all"])
    if not result["ok"]:
        return []
    rows = []
    for line in result["stdout"].splitlines()[2:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 3:
            rows.append({"name": parts[0], "state": parts[1], "autostart": parts[2]})
    return rows


def list_iso_files() -> list[dict[str, str]]:
    ISO_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for item in sorted(ISO_DIR.glob("*.iso")):
        try:
            size_mb = item.stat().st_size / 1024 / 1024
        except OSError:
            size_mb = 0
        files.append({"name": item.name, "path": str(item), "size": f"{size_mb:.1f} MB"})
    return files


def valid_vm_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,62}", name or ""))


def vm_exists(name: str) -> bool:
    return run_cmd(["virsh", "dominfo", name], timeout=8)["ok"]


def system_summary() -> dict[str, str]:
    hostname = run_cmd(["hostname"])["stdout"]
    uptime = run_cmd(["uptime", "-p"])["stdout"]
    kernel = run_cmd(["uname", "-r"])["stdout"]
    ip_addr = run_cmd(["hostname", "-I"])["stdout"].split()
    ip_main = ip_addr[0] if ip_addr else "unknown"
    load = Path("/proc/loadavg").read_text().split()[:3]
    return {
        "hostname": hostname,
        "uptime": uptime,
        "kernel": kernel,
        "ip": ip_main,
        "load": " ".join(load),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def service_state(unit: str) -> str:
    result = run_cmd(["systemctl", "is-active", unit])
    return result["stdout"] or "inactive"


def network_summary() -> dict[str, str]:
    return {
        "interfaces": run_cmd(["ip", "-br", "a"])["stdout"],
        "routes": run_cmd(["ip", "route"])["stdout"],
    }


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "error": None,
            "configured": is_configured(),
            "auth_user": AUTH_USER,
        },
    )


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if not is_configured():
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "app_name": APP_NAME,
                "error": "Панель ещё не настроена. Запусти установщик веб-панели повторно.",
                "configured": False,
                "auth_user": AUTH_USER,
            },
            status_code=500,
        )
    if not verify_linux_password(username, password):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "app_name": APP_NAME,
                "error": "Неверный логин или пароль Linux-пользователя",
                "configured": True,
                "auth_user": AUTH_USER,
            },
            status_code=401,
        )
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
    vms = parse_virsh_list()
    pools = parse_pool_list()
    services = {
        "libvirtd": service_state("libvirtd.service"),
        "virtlogd": service_state("virtlogd.service"),
        "cockpit": service_state("cockpit.socket"),
        "dashboard": service_state("virtuality-console-dashboard.service"),
        "web": service_state("virtuality-web.service"),
    }
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "system": system_summary(),
            "services": services,
            "vms": vms,
            "pools": pools,
            "network": network_summary(),
            "user": AUTH_USER,
        },
    )


@app.get("/vm/create", response_class=HTMLResponse)
def vm_create_page(request: Request):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    return templates.TemplateResponse(
        "vm_create.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "user": AUTH_USER,
            "isos": list_iso_files(),
            "error": None,
            "form": {"memory": 2048, "vcpus": 2, "disk_size": 20, "bridge": DEFAULT_BRIDGE},
        },
    )


@app.post("/vm/create", response_class=HTMLResponse)
def vm_create_submit(
    request: Request,
    name: str = Form(...),
    memory: int = Form(...),
    vcpus: int = Form(...),
    disk_size: int = Form(...),
    iso_path: str = Form(...),
    bridge: str = Form(DEFAULT_BRIDGE),
    autostart_install: str | None = Form(None),
):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect

    form = {"name": name, "memory": memory, "vcpus": vcpus, "disk_size": disk_size, "iso_path": iso_path, "bridge": bridge}
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
    elif not bridge or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", bridge):
        error = "Некорректное имя bridge."
    else:
        iso = Path(iso_path).resolve()
        iso_root = ISO_DIR.resolve()
        if iso_root not in iso.parents or iso.suffix.lower() != ".iso" or not iso.exists():
            error = "ISO должен быть существующим .iso файлом из /var/lib/virtuality/iso."

    if error:
        return templates.TemplateResponse(
            "vm_create.html",
            {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "isos": list_iso_files(), "error": error, "form": form},
            status_code=400,
        )

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    disk_path = IMAGES_DIR / f"{name}.qcow2"
    if disk_path.exists():
        return templates.TemplateResponse(
            "vm_create.html",
            {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "isos": list_iso_files(), "error": f"Диск уже существует: {disk_path}", "form": form},
            status_code=400,
        )

    cmd = [
        "virt-install",
        "--name", name,
        "--memory", str(memory),
        "--vcpus", str(vcpus),
        "--disk", f"path={disk_path},size={disk_size},format=qcow2,bus=virtio",
        "--cdrom", iso_path,
        "--os-variant", "generic",
        "--network", f"bridge={bridge},model=virtio",
        "--graphics", "vnc,listen=0.0.0.0",
        "--noautoconsole",
    ]
    if not autostart_install:
        # virt-install starts the installer by design; this flag is kept for future behaviour toggles.
        pass

    result = run_cmd(cmd, timeout=180)
    if not result["ok"]:
        return templates.TemplateResponse(
            "vm_create.html",
            {"request": request, "app_name": APP_NAME, "user": AUTH_USER, "isos": list_iso_files(), "error": result["stderr"] or result["stdout"] or "virt-install failed", "form": form},
            status_code=500,
        )

    run_cmd(["virsh", "pool-refresh", "virtuality-images"], timeout=20)
    return RedirectResponse(url="/", status_code=303)


@app.post("/vm/{name}/{action}")
def vm_action(request: Request, name: str, action: str):
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    allowed = {
        "start": ["virsh", "start", name],
        "shutdown": ["virsh", "shutdown", name],
        "reboot": ["virsh", "reboot", name],
        "destroy": ["virsh", "destroy", name],
    }
    if action not in allowed:
        return JSONResponse({"ok": False, "error": "Unsupported action"}, status_code=400)
    run_cmd(allowed[action], timeout=20)
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/health")
def api_health(request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    return {
        "system": system_summary(),
        "services": {
            "libvirtd": service_state("libvirtd.service"),
            "virtlogd": service_state("virtlogd.service"),
            "cockpit": service_state("cockpit.socket"),
            "dashboard": service_state("virtuality-console-dashboard.service"),
            "web": service_state("virtuality-web.service"),
        },
        "vms": parse_virsh_list(),
        "pools": parse_pool_list(),
        "network": network_summary(),
    }
