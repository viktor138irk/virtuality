"""Settings page: everything the setup wizard configures, changeable later in one place."""
import re
from pathlib import Path
from typing import Any

import network_core
import nodectl
from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse
from core import APP_VERSION, AUTH_USER, redirect_with_message, render, require_auth

router = APIRouter()


def page(request: Request, error: str | None = None, message: str | None = None, status_code: int = 200):
    settings = nodectl.node_settings()
    host = request.url.hostname or "127.0.0.1"
    context: dict[str, Any] = {
        "page_title": "Настройки", "error": error, "message": message, "settings": settings, "auth_user": AUTH_USER,
        "tls": settings.get("VIRTUALITY_TLS") == "1", "port": settings.get("VIRTUALITY_WEB_PORT", "8088"), "tls_port": settings.get("VIRTUALITY_TLS_PORT", "8443"),
        "auto_update": settings.get("VIRTUALITY_AUTO_UPDATE", "1") == "1", "channel": settings.get("VIRTUALITY_UPDATE_CHANNEL", "stable"),
        "timezones": nodectl.timezones(), "net": nodectl.network_facts(), "storage": nodectl.storage_facts(),
        "hardware": nodectl.hardware_summary(), "ctl": nodectl.ctl_available(), "version": APP_VERSION,
        "panel_url": nodectl.panel_url(host, settings), "source_dir": settings.get("VIRTUALITY_SOURCE_DIR", "/opt/virtuality/source"),
    }
    return render(request, "settings.html", context, status_code=status_code)


@router.get("/settings")
def settings_page(request: Request, message: str = "", error: str = ""):
    auth = require_auth(request)
    if auth:
        return auth
    return page(request, error=error or None, message=message or None)


@router.post("/settings/access")
def settings_access(request: Request, tls: str = Form("0"), port: str = Form("8088")):
    auth = require_auth(request)
    if auth:
        return auth
    if not re.fullmatch(r"\d{1,5}", port) or not 1 <= int(port) <= 65535 or int(port) in (22, 9090):
        return page(request, error="Порт должен быть числом от 1 до 65535 (кроме 22 и 9090)", status_code=400)
    settings = nodectl.node_settings()
    tls = "1" if tls == "1" else "0"
    if tls == settings.get("VIRTUALITY_TLS", "0") and port == settings.get("VIRTUALITY_WEB_PORT", "8088"):
        return redirect_with_message("/settings", "message", "Доступ уже настроен так")
    ok, detail = nodectl.apply_settings({"VIRTUALITY_TLS": tls, "VIRTUALITY_WEB_PORT": port})
    if not ok:
        return page(request, error=detail, status_code=500)
    host = request.url.hostname or "127.0.0.1"
    final_url = f"https://{host}:{settings.get('VIRTUALITY_TLS_PORT', '8443')}" if tls == "1" else f"http://{host}:{port}"
    return render(request, "setup_applying.html", {"page_title": "Применяем настройки", "final_url": final_url, "tls": tls == "1"})


@router.post("/settings/password")
def settings_password(request: Request, password: str = Form(""), password2: str = Form("")):
    auth = require_auth(request)
    if auth:
        return auth
    if password != password2:
        return page(request, error="Пароли не совпадают", status_code=400)
    ok, detail = nodectl.change_password(AUTH_USER, password)
    if not ok:
        return page(request, error=detail, status_code=400)
    return redirect_with_message("/settings", "message", detail)


@router.post("/settings/timezone")
def settings_timezone(request: Request, timezone: str = Form("")):
    auth = require_auth(request)
    if auth:
        return auth
    ok, detail = nodectl.set_timezone(timezone)
    if not ok:
        return page(request, error=detail, status_code=400)
    return redirect_with_message("/settings", "message", f"Часовой пояс: {detail}")


@router.post("/settings/updates")
def settings_updates(request: Request, auto_update: str = Form("0"), channel: str = Form("stable")):
    auth = require_auth(request)
    if auth:
        return auth
    if channel not in ("stable", "main"):
        channel = "stable"
    ok, detail = nodectl.apply_settings({"VIRTUALITY_AUTO_UPDATE": "1" if auto_update == "1" else "0", "VIRTUALITY_UPDATE_CHANNEL": channel})
    return redirect_with_message("/settings", "message" if ok else "error", "Настройки обновлений сохранены" if ok else detail)


@router.post("/settings/network")
def settings_network(request: Request, mode: str = Form("nat")):
    auth = require_auth(request)
    if auth:
        return auth
    facts = nodectl.network_facts()
    if mode == "nat":
        try:
            network_core.create_nat_network()
        except network_core.NetworkError as exc:
            return page(request, error=f"Не удалось создать сеть машин: {exc}", status_code=500)
        return redirect_with_message("/settings", "message", "Сеть NAT готова. Новые машины будут подключаться к ней.")
    if mode != "bridge":
        return page(request, error="Выберите режим сети", status_code=400)
    if facts["on_bridge"]:
        return redirect_with_message("/settings", "message", "Сервер уже подключён через мост br0")
    if not facts["bridge_possible"]:
        return page(request, error="Мост недоступен: Wi-Fi или не найден проводной интерфейс", status_code=400)
    ok, detail = nodectl.enable_bridge(facts["interface"], "dhcp")
    if not ok:
        return page(request, error=detail, status_code=500)
    return render(request, "setup_bridge_wait.html", {"page_title": "Перенастройка сети", "next_url": "/settings", "confirm_url": "/setup/network/confirm", "revert_url": "/settings/network/revert", "back_url": "/settings"})


@router.post("/settings/network/revert")
def settings_network_revert(request: Request):
    auth = require_auth(request)
    if auth:
        return auth
    ok, detail = nodectl.revert_bridge()
    return redirect_with_message("/settings", "message" if ok else "error", detail)


@router.post("/settings/storage")
def settings_storage(request: Request, disk: str = Form("")):
    auth = require_auth(request)
    if auth:
        return auth
    ok, detail = nodectl.use_disk_for_storage(disk)
    return redirect_with_message("/settings", "message" if ok else "error", detail)


@router.post("/settings/power")
def settings_power(request: Request, action: str = Form("")):
    auth = require_auth(request)
    if auth:
        return auth
    ok, detail = nodectl.power(action)
    if not ok:
        return page(request, error=detail, status_code=400)
    return render(request, "settings_power.html", {"page_title": "Сервер", "action": action, "message": detail})


@router.post("/settings/backup-config")
def settings_backup_config(request: Request):
    auth = require_auth(request)
    if auth:
        return auth
    ok, detail = nodectl.backup_config()
    if not ok or not Path(detail).is_file():
        return page(request, error=detail if not ok else "Архив не найден", status_code=500)
    return FileResponse(detail, media_type="application/gzip", filename=Path(detail).name)


@router.post("/settings/support-bundle")
def settings_support_bundle(request: Request):
    auth = require_auth(request)
    if auth:
        return auth
    ok, detail = nodectl.support_bundle()
    if not ok or not Path(detail).is_file():
        return page(request, error=detail if not ok else "Отчёт не найден", status_code=500)
    return FileResponse(detail, media_type="application/gzip", filename=Path(detail).name)



