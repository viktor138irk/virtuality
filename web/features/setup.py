"""First-run setup wizard (/setup) and the "applying settings" page."""
import re
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

import network_core
import nodectl
import presenters
from core import AUTH_USER, cmd_error, get_current_user, redirect_with_message, render, require_auth, run_cmd

router = APIRouter()

STEPS = [
    {"id": "install", "title": "Установка", "hint": "Компоненты сервера"},
    {"id": "welcome", "title": "Сервер", "hint": "Проверка и время"},
    {"id": "account", "title": "Администратор", "hint": "Вход в панель"},
    {"id": "access", "title": "Доступ", "hint": "Адрес и HTTPS"},
    {"id": "network", "title": "Сеть машин", "hint": "NAT или домашняя сеть"},
    {"id": "storage", "title": "Хранилище", "hint": "Где живут машины"},
    {"id": "updates", "title": "Обновления", "hint": "Как обновляться"},
    {"id": "finish", "title": "Готово", "hint": "Проверка и запуск"},
]
STEP_IDS = [step["id"] for step in STEPS]


def wizard_pending() -> bool:
    """The wizard is offered until finished on nodes set up by the installer or the ISO."""
    return not nodectl.wizard_done() and nodectl.SETUP_STATE.exists()


def step_index(step_id: str) -> int:
    return STEP_IDS.index(step_id) if step_id in STEP_IDS else 0


def next_step(step_id: str) -> str:
    index = step_index(step_id)
    return STEP_IDS[min(index + 1, len(STEP_IDS) - 1)]


def render_step(request: Request, step_id: str, error: str | None = None, message: str | None = None, status_code: int = 200):
    choices = nodectl.wizard_choices()
    settings = nodectl.node_settings()
    state = nodectl.setup_state()
    host = request.url.hostname or "127.0.0.1"
    context: dict[str, Any] = {
        "steps": STEPS, "step": step_id, "step_index": step_index(step_id), "choices": choices, "settings": settings,
        "state": state, "error": error, "message": message, "host": host, "auth_user": AUTH_USER,
        "page_title": "Мастер настройки",
    }
    if step_id == "welcome":
        context["hardware"] = nodectl.hardware_summary()
        context["timezones"] = nodectl.timezones()
        context["profile_ready"] = context["hardware"]["kvm"]
    elif step_id == "access":
        context["tls"] = choices.get("tls", settings.get("VIRTUALITY_TLS", "0") == "1" or not settings.get("VIRTUALITY_TLS_SET"))
        context["port"] = choices.get("port", settings.get("VIRTUALITY_WEB_PORT", "8088"))
    elif step_id == "network":
        context["net"] = nodectl.network_facts()
        context["nat_ready"] = network_core.libvirt_network_info()["exists"]
    elif step_id == "storage":
        context["storage"] = nodectl.storage_facts()
    elif step_id == "finish":
        context["net"] = nodectl.network_facts()
        context["hardware"] = nodectl.hardware_summary()
        context["storage"] = nodectl.storage_facts()
        context["timezone"] = choices.get("timezone") or nodectl.timezones()["current"]
        tls = str(choices.get("tls", "1")) == "1"
        port = str(choices.get("port", settings.get("VIRTUALITY_WEB_PORT", "8088")))
        context["final_url"] = f"https://{host}:{settings.get('VIRTUALITY_TLS_PORT', '8443')}" if tls else f"http://{host}:{port}"
        context["restart_needed"] = (tls != (settings.get("VIRTUALITY_TLS") == "1")) or port != settings.get("VIRTUALITY_WEB_PORT", "8088") or str(choices.get("auto_update", settings.get("VIRTUALITY_AUTO_UPDATE", "1"))) != settings.get("VIRTUALITY_AUTO_UPDATE", "1") or str(choices.get("channel", settings.get("VIRTUALITY_UPDATE_CHANNEL", "stable"))) != settings.get("VIRTUALITY_UPDATE_CHANNEL", "stable")
    return render(request, "setup.html", context, status_code=status_code)


@router.get("/setup")
def setup_index(request: Request):
    auth = require_auth(request)
    if auth:
        return auth
    choices = nodectl.wizard_choices()
    state = nodectl.setup_state()
    if not state["installed"]:
        return RedirectResponse(url="/setup/install", status_code=303)
    return RedirectResponse(url=f"/setup/{choices.get('step', 'welcome')}", status_code=303)


@router.get("/setup/{step_id}")
def setup_step(request: Request, step_id: str, message: str = "", error: str = ""):
    auth = require_auth(request)
    if auth:
        return auth
    if step_id not in STEP_IDS:
        return RedirectResponse(url="/setup", status_code=303)
    if step_id != "install" and not nodectl.setup_state()["installed"]:
        return RedirectResponse(url="/setup/install", status_code=303)
    if step_id != "install":
        nodectl.save_wizard_choices(step=step_id)
    return render_step(request, step_id, error=error or None, message=message or None)


@router.get("/api/setup/state")
def setup_state_api(request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    state = nodectl.setup_state()
    return {"ok": True, "state": state, "net": {"revert_armed": nodectl.network_facts()["revert_armed"]} if request.query_params.get("net") else None}


# ---------------------------------------------------------------- step handlers
@router.post("/setup/welcome")
def setup_welcome(request: Request, timezone: str = Form("")):
    auth = require_auth(request)
    if auth:
        return auth
    if timezone:
        ok, detail = nodectl.set_timezone(timezone)
        if not ok:
            return render_step(request, "welcome", error=detail, status_code=400)
        nodectl.save_wizard_choices(timezone=timezone)
    return RedirectResponse(url="/setup/account", status_code=303)


@router.post("/setup/account")
def setup_account(request: Request, password: str = Form(""), password2: str = Form("")):
    auth = require_auth(request)
    if auth:
        return auth
    if password or password2:
        if password != password2:
            return render_step(request, "account", error="Пароли не совпадают", status_code=400)
        ok, detail = nodectl.change_password(AUTH_USER, password)
        if not ok:
            return render_step(request, "account", error=detail, status_code=400)
        nodectl.save_wizard_choices(password_changed=True)
    return RedirectResponse(url="/setup/access", status_code=303)


@router.post("/setup/access")
def setup_access(request: Request, tls: str = Form("1"), port: str = Form("8088")):
    auth = require_auth(request)
    if auth:
        return auth
    if not re.fullmatch(r"\d{2,5}", port) or not 1 <= int(port) <= 65535 or int(port) in (22, 9090):
        return render_step(request, "access", error="Порт должен быть числом от 1 до 65535 (кроме 22 и 9090)", status_code=400)
    nodectl.save_wizard_choices(tls="1" if tls == "1" else "0", port=port)
    return RedirectResponse(url="/setup/network", status_code=303)


@router.post("/setup/network")
def setup_network(request: Request, mode: str = Form("nat")):
    auth = require_auth(request)
    if auth:
        return auth
    if mode not in ("nat", "bridge"):
        return render_step(request, "network", error="Выберите режим сети", status_code=400)
    if mode == "nat":
        try:
            network_core.create_nat_network()
        except network_core.NetworkError as exc:
            return render_step(request, "network", error=f"Не удалось создать сеть машин: {exc}", status_code=500)
        nodectl.save_wizard_choices(network="nat")
        return RedirectResponse(url="/setup/storage", status_code=303)
    facts = nodectl.network_facts()
    if facts["on_bridge"]:
        nodectl.save_wizard_choices(network="bridge")
        return RedirectResponse(url="/setup/storage", status_code=303)
    if not facts["bridge_possible"]:
        return render_step(request, "network", error="На этом сервере мост недоступен (Wi-Fi или не найден проводной интерфейс). Выберите NAT.", status_code=400)
    ok, detail = nodectl.enable_bridge(facts["interface"], "dhcp")
    if not ok:
        return render_step(request, "network", error=detail, status_code=500)
    nodectl.save_wizard_choices(network="bridge", bridge_pending=True)
    return render(request, "setup_bridge_wait.html", {"page_title": "Перенастройка сети", "next_url": "/setup/storage", "confirm_url": "/setup/network/confirm", "revert_url": "/setup/network/revert", "back_url": "/setup/network"})


@router.post("/setup/network/confirm")
def setup_network_confirm(request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    ok, detail = nodectl.confirm_bridge()
    if ok:
        nodectl.save_wizard_choices(bridge_pending=False, network="bridge")
        profile = None
        try:
            import host_profile

            profile = host_profile.detect_host_profile()
            host_profile.save_host_profile(profile)
        except Exception:
            pass
    return JSONResponse({"ok": ok, "message": detail})


@router.post("/setup/network/revert")
def setup_network_revert(request: Request):
    auth = require_auth(request)
    if auth:
        return auth
    ok, detail = nodectl.revert_bridge()
    nodectl.save_wizard_choices(bridge_pending=False, network="nat")
    return redirect_with_message("/setup/network", "message" if ok else "error", detail)


@router.post("/setup/storage")
def setup_storage(request: Request, disk: str = Form("")):
    auth = require_auth(request)
    if auth:
        return auth
    if disk and disk != "keep":
        ok, detail = nodectl.use_disk_for_storage(disk)
        if not ok:
            return render_step(request, "storage", error=detail, status_code=400)
        nodectl.save_wizard_choices(storage_disk=disk)
    return RedirectResponse(url="/setup/updates", status_code=303)


@router.post("/setup/updates")
def setup_updates(request: Request, auto_update: str = Form("1"), channel: str = Form("stable")):
    auth = require_auth(request)
    if auth:
        return auth
    if channel not in ("stable", "main"):
        channel = "stable"
    nodectl.save_wizard_choices(auto_update="1" if auto_update == "1" else "0", channel=channel)
    return RedirectResponse(url="/setup/finish", status_code=303)


@router.post("/setup/finish")
def setup_finish(request: Request):
    auth = require_auth(request)
    if auth:
        return auth
    choices = nodectl.wizard_choices()
    settings = nodectl.node_settings()
    host = request.url.hostname or "127.0.0.1"
    changes = {
        "VIRTUALITY_TLS": str(choices.get("tls", "1")),
        "VIRTUALITY_WEB_PORT": str(choices.get("port", settings.get("VIRTUALITY_WEB_PORT", "8088"))),
        "VIRTUALITY_AUTO_UPDATE": str(choices.get("auto_update", settings.get("VIRTUALITY_AUTO_UPDATE", "1"))),
        "VIRTUALITY_UPDATE_CHANNEL": str(choices.get("channel", settings.get("VIRTUALITY_UPDATE_CHANNEL", "stable"))),
    }
    current = {"VIRTUALITY_TLS": settings.get("VIRTUALITY_TLS", "0"), "VIRTUALITY_WEB_PORT": settings.get("VIRTUALITY_WEB_PORT", "8088"), "VIRTUALITY_AUTO_UPDATE": settings.get("VIRTUALITY_AUTO_UPDATE", "1"), "VIRTUALITY_UPDATE_CHANNEL": settings.get("VIRTUALITY_UPDATE_CHANNEL", "stable")}
    nodectl.mark_wizard_done()
    nodectl.save_wizard_choices(step="finish", finished=True)
    if changes != current and nodectl.ctl_available():
        ok, detail = nodectl.apply_settings(changes)
        if not ok:
            return render_step(request, "finish", error=detail, status_code=500)
        final_url = f"https://{host}:{settings.get('VIRTUALITY_TLS_PORT', '8443')}" if changes["VIRTUALITY_TLS"] == "1" else f"http://{host}:{changes['VIRTUALITY_WEB_PORT']}"
        return render(request, "setup_applying.html", {"page_title": "Применяем настройки", "final_url": final_url, "tls": changes["VIRTUALITY_TLS"] == "1"})
    return redirect_with_message("/", "message", "Сервер настроен. Можно создавать машины.")


@router.get("/setup/skip")
def setup_skip(request: Request):
    auth = require_auth(request)
    if auth:
        return auth
    nodectl.mark_wizard_done()
    return redirect_with_message("/", "message", "Мастер пропущен. Его можно запустить позже в Настройках.")


@router.post("/setup/restart")
def setup_restart(request: Request):
    """Re-run the wizard from Settings."""
    auth = require_auth(request)
    if auth:
        return auth
    try:
        nodectl.SETUP_DONE.unlink()
    except OSError:
        pass
    nodectl.save_wizard_choices(step="welcome", finished=False)
    if not nodectl.SETUP_STATE.exists():
        nodectl.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        nodectl.SETUP_STATE.write_text('{"stage": "done", "steps": []}\n')
    return RedirectResponse(url="/setup/welcome", status_code=303)


def virtualization_hint() -> str:
    result = run_cmd(["systemd-detect-virt"], timeout=5)
    return cmd_error(result, "") if not result["ok"] else result["stdout"]


def format_bytes(value: int) -> str:
    return presenters.format_bytes(value)
