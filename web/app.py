#!/usr/bin/env python3
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = "Virtuality"

app = FastAPI(title="Virtuality Panel")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


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


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    vms = parse_virsh_list()
    pools = parse_pool_list()
    services = {
        "libvirtd": service_state("libvirtd.service"),
        "virtlogd": service_state("virtlogd.service"),
        "cockpit": service_state("cockpit.socket"),
        "dashboard": service_state("virtuality-console-dashboard.service"),
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
        },
    )


@app.post("/vm/{name}/{action}")
def vm_action(name: str, action: str):
    allowed = {
        "start": ["virsh", "start", name],
        "shutdown": ["virsh", "shutdown", name],
        "reboot": ["virsh", "reboot", name],
        "destroy": ["virsh", "destroy", name],
    }
    if action not in allowed:
        return JSONResponse({"ok": False, "error": "Unsupported action"}, status_code=400)
    result = run_cmd(allowed[action], timeout=20)
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/health")
def api_health():
    return {
        "system": system_summary(),
        "services": {
            "libvirtd": service_state("libvirtd.service"),
            "virtlogd": service_state("virtlogd.service"),
            "cockpit": service_state("cockpit.socket"),
            "dashboard": service_state("virtuality-console-dashboard.service"),
        },
        "vms": parse_virsh_list(),
        "pools": parse_pool_list(),
        "network": network_summary(),
    }
