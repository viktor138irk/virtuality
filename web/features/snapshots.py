"""«Снимки» (точки восстановления) виртуальной машины — внутренние снимки
libvirt через `virsh snapshot-*`.

Снимок хранится внутри qcow2-диска машины. У работающей машины в снимок
попадает и память (машина на несколько секунд замирает), у выключенной —
только диск. Создание и возврат выполняются как фоновые операции.
"""
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

import presenters
from core import (
    active_operations_for,
    append_operation_log,
    cmd_error,
    finish_operation,
    list_operations,
    new_operation,
    read_operation,
    redirect_with_message,
    render,
    require_auth,
    run_cmd,
    run_operation,
    update_operation,
    valid_label,
    valid_vm_name,
)

router = APIRouter()

MAX_SNAPSHOTS = 8
DESCRIPTION_MAX = 200
SNAPSHOT_TIMEOUT = 1800  # секунд: снимок памяти большой машины идёт минуты
OPERATION_TYPES = ("snapshot_create", "snapshot_revert")

SNAPSHOT_STATES = {
    "running": ("Работала", "success"),
    "paused": ("Была на паузе", "warning"),
    "shutoff": ("Была выключена", "neutral"),
    "disk-snapshot": ("Только диск", "info"),
}

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]

# Известные ошибки libvirt → понятное объяснение (подстрока ищется без учёта регистра).
KNOWN_ERRORS = [
    ("pflash", "Для машин с UEFI-прошивкой эта версия libvirt не умеет делать внутренние снимки."),
    ("nvram", "Для машин с UEFI-прошивкой эта версия libvirt не умеет делать внутренние снимки."),
    ("unsupported for storage type", "Внутренние снимки работают только с дисками qcow2."),
    ("does not support snapshot", "Внутренние снимки работают только с дисками qcow2."),
    ("internal snapshot for disk", "Внутренние снимки работают только с дисками qcow2."),
    ("cannot acquire state change lock", "Машина занята другой операцией. Подождите минуту и попробуйте снова."),
    ("timed out during operation", "Машина занята другой операцией. Подождите минуту и попробуйте снова."),
    ("snapshot not found", "Такого снимка уже нет — возможно, его удалили."),
    ("already exists", "Снимок с таким именем уже есть."),
    ("no space left", "На диске сервера закончилось место."),
    ("is not running", "Машина выключена, поэтому это действие сейчас недоступно."),
]

_ROW_RE = re.compile(r"^\s*(\S.*?)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})(?:\s+([+-]\d{4}))?\s+(\S+)\s*$")


# ---------------------------------------------------------------- parsers
def parse_snapshot_list(text: str) -> list[dict[str, Any]]:
    """Таблица `virsh snapshot-list` → список снимков, новые сверху."""
    rows = []
    for line in (text or "").splitlines():
        match = _ROW_RE.match(line)
        if not match:
            continue
        name, date, time, tz, state = match.groups()
        rows.append({"name": name, "created_at": f"{date} {time}", "tz": tz or "", "state": state})
    return sorted(rows, key=lambda row: row["created_at"], reverse=True)


def parse_snapshot_xml(text: str) -> dict[str, Any]:
    """`virsh snapshot-dumpxml` → описание, состояние, есть ли память."""
    try:
        root = ET.fromstring(text or "")
    except ET.ParseError:
        return {}
    memory = root.find("memory")
    parent = root.find("parent/name")
    return {
        "description": (root.findtext("description") or "").strip(),
        "state": (root.findtext("state") or "").strip(),
        "with_memory": memory is not None and memory.get("snapshot", "no") not in ("no", ""),
        "parent": (parent.text or "").strip() if parent is not None else "",
    }


def inspect_domain_xml(text: str) -> dict[str, Any]:
    """`virsh dumpxml` → форматы дисков и признак UEFI (для проверок перед снимком)."""
    info: dict[str, Any] = {"disks": [], "unsupported": [], "uefi": False}
    try:
        root = ET.fromstring(text or "")
    except ET.ParseError:
        return info
    for disk in root.iter("disk"):
        if disk.get("device", "disk") != "disk":
            continue
        driver = disk.find("driver")
        target = disk.find("target")
        fmt = (driver.get("type") if driver is not None else "") or ""
        dev = (target.get("dev") if target is not None else "") or "?"
        info["disks"].append({"target": dev, "format": fmt or "неизвестно"})
        if fmt and fmt != "qcow2":
            info["unsupported"].append({"target": dev, "format": fmt})
    os_node = root.find("os")
    if os_node is not None:
        loader = os_node.find("loader")
        info["uefi"] = os_node.get("firmware") == "efi" or os_node.find("nvram") is not None or (loader is not None and loader.get("type") == "pflash")
    return info


def snapshot_state(state: str) -> dict[str, str]:
    label, tone = SNAPSHOT_STATES.get((state or "").strip().lower(), (state or "—", "neutral"))
    return {"label": label, "tone": tone}


def format_when(created_at: str, now: datetime | None = None) -> str:
    """'2026-09-25 10:12:03' → 'Сегодня, 10:12' / 'Вчера, 10:12' / '25 сентября 2026, 10:12'."""
    try:
        moment = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return created_at or "—"
    today = (now or datetime.now()).date()
    clock = moment.strftime("%H:%M")
    if moment.date() == today:
        return f"Сегодня, {clock}"
    if moment.date() == today - timedelta(days=1):
        return f"Вчера, {clock}"
    return f"{moment.day} {MONTHS[moment.month - 1]} {moment.year}, {clock}"


def explain_error(raw: str) -> str:
    """Текст ошибки libvirt → понятная фраза; технический текст остаётся в скобках."""
    text = re.sub(r"^error:\s*", "", (raw or "").strip(), flags=re.IGNORECASE)
    first = text.splitlines()[0] if text else ""
    low = first.lower()
    for needle, explanation in KNOWN_ERRORS:
        if needle in low:
            return f"{explanation} (libvirt: {first})"
    return first or "libvirt не выполнил команду и не объяснил причину."


def next_snapshot_name(existing: list[str], now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("snap-%Y%m%d-%H%M")
    name, counter = stamp, 2
    while name in existing:
        name, counter = f"{stamp}-{counter}", counter + 1
    return name


def clean_description(text: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text or "")
    return re.sub(r"\s+", " ", text).strip().lstrip("-").strip()[:DESCRIPTION_MAX]


# ---------------------------------------------------------------- virsh
def current_snapshot(vm: str) -> str:
    result = run_cmd(["virsh", "snapshot-current", "--domain", vm, "--name"], timeout=10)
    return result["stdout"].strip() if result["ok"] else ""


def list_snapshots(vm: str) -> list[dict[str, Any]]:
    result = run_cmd(["virsh", "snapshot-list", "--domain", vm], timeout=20)
    rows = parse_snapshot_list(result["stdout"]) if result["ok"] else []
    current = current_snapshot(vm) if rows else ""
    for row in rows:
        xml = run_cmd(["virsh", "snapshot-dumpxml", "--domain", vm, "--snapshotname", row["name"]], timeout=10)
        meta = parse_snapshot_xml(xml["stdout"]) if xml["ok"] else {}
        row["description"] = meta.get("description", "")
        row["with_memory"] = meta.get("with_memory", row["state"] == "running")
        row["is_current"] = row["name"] == current
        row["state_info"] = snapshot_state(row["state"])
        row["when"] = format_when(row["created_at"])
        row["title"] = row["description"] or row["name"]
        # Snapshots made outside the panel (virsh, virt-manager) may have names the panel's URLs do not accept.
        row["manageable"] = valid_label(row["name"])
    return rows


def domain_checks(vm: str) -> dict[str, Any]:
    result = run_cmd(["virsh", "dumpxml", vm], timeout=10)
    return inspect_domain_xml(result["stdout"] if result["ok"] else "")


def vm_operations(vm: str) -> list[dict[str, Any]]:
    return [op for op in list_operations(50) if op.get("type") in OPERATION_TYPES and op.get("vm_name") == vm]


def active_operations(vm: str) -> list[dict[str, Any]]:
    return active_operations_for(vm, OPERATION_TYPES)


def create_blockers(vm: str, snapshots: list[dict[str, Any]], checks: dict[str, Any]) -> list[str]:
    """Почему снимок сейчас нельзя создать (пустой список — можно)."""
    reasons = []
    if len(snapshots) >= MAX_SNAPSHOTS:
        reasons.append(f"Уже {MAX_SNAPSHOTS} снимков — это максимум для одной машины. Удалите ненужный снимок, чтобы создать новый.")
    if checks.get("unsupported"):
        disks = ", ".join(f"{d['target']} ({d['format']})" for d in checks["unsupported"])
        reasons.append(f"Внутренние снимки работают только с дисками qcow2, а у этой машины диск другого формата: {disks}.")
    if active_operations(vm):
        reasons.append("Дождитесь окончания предыдущей операции со снимками этой машины.")
    return reasons


def _log_result(operation: dict[str, Any], result: dict[str, Any]) -> None:
    for stream in ("stdout", "stderr"):
        if result.get(stream):
            append_operation_log(operation["id"], result[stream])


def create_worker(operation: dict[str, Any]) -> None:
    vm, snap, description = operation["vm_name"], operation["snapshot"], operation.get("description", "")
    cmd = ["virsh", "snapshot-create-as", "--domain", vm, "--name", snap]
    if description:
        cmd += ["--description", description]
    cmd.append("--atomic")
    append_operation_log(operation["id"], "$ " + " ".join(cmd))
    update_operation(operation, progress=15, message="Сохраняем состояние машины и памяти — она на несколько секунд замрёт…" if operation.get("vm_running") else "Сохраняем состояние диска…", cmd=" ".join(cmd))
    result = run_cmd(cmd, timeout=SNAPSHOT_TIMEOUT)
    _log_result(operation, result)
    if result["ok"]:
        finish_operation(operation, True, f"Снимок «{snap}» создан")
    else:
        finish_operation(operation, False, explain_error(cmd_error(result, "virsh завершился с ошибкой")))


def revert_worker(operation: dict[str, Any]) -> None:
    vm, snap = operation["vm_name"], operation["snapshot"]
    cmd = ["virsh", "snapshot-revert", "--domain", vm, "--snapshotname", snap]
    append_operation_log(operation["id"], "$ " + " ".join(cmd))
    update_operation(operation, progress=15, message="Возвращаем машину к сохранённому состоянию…", cmd=" ".join(cmd))
    result = run_cmd(cmd, timeout=SNAPSHOT_TIMEOUT)
    _log_result(operation, result)
    if not result["ok"] and "revert requires force" in (result.get("stderr") or "").lower():
        # libvirt просит --force, когда конфигурация машины изменилась после снимка: пользователь уже подтвердил возврат.
        append_operation_log(operation["id"], "libvirt требует подтверждения (--force): повторяем принудительно.")
        cmd.append("--force")
        append_operation_log(operation["id"], "$ " + " ".join(cmd))
        result = run_cmd(cmd, timeout=SNAPSHOT_TIMEOUT)
        _log_result(operation, result)
    if not result["ok"]:
        finish_operation(operation, False, explain_error(cmd_error(result, "virsh завершился с ошибкой")))
        return
    state = presenters.vm_state(run_cmd(["virsh", "domstate", vm], timeout=8)["stdout"])
    finish_operation(operation, True, f"Машина возвращена к снимку «{snap}». Сейчас она {'работает' if state['running'] else 'выключена'}.")


# ---------------------------------------------------------------- routes
def _guard(request: Request, name: str):
    """Редирект на /login, 400 для плохого имени, редирект на / для несуществующей машины — или None + dominfo."""
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect, ""
    if not valid_vm_name(name):
        return JSONResponse({"ok": False, "error": "Invalid VM name"}, status_code=400), ""
    dominfo = run_cmd(["virsh", "dominfo", name], timeout=8)
    if not dominfo["ok"]:
        return RedirectResponse(url="/", status_code=303), ""
    return None, dominfo["stdout"]


@router.get("/vm/{name}/snapshots")
def snapshots_page(request: Request, name: str):
    blocked, dominfo = _guard(request, name)
    if blocked:
        return blocked
    info = presenters.parse_dominfo(dominfo)
    snapshots = list_snapshots(name)
    checks = domain_checks(name)
    running = vm_active(info["state"])
    op_id = request.query_params.get("op", "")
    operation = read_operation(op_id) if op_id else None
    if operation and (operation.get("vm_name") != name or operation.get("type") not in OPERATION_TYPES):
        operation = None
    return render(request, "vm_snapshots.html", {
        "vm": {"name": name, "info": info, "dominfo": dominfo},
        "vm_running": running,
        "snapshots": snapshots,
        "max_snapshots": MAX_SNAPSHOTS,
        "checks": checks,
        "blockers": create_blockers(name, snapshots, checks),
        "suggested_name": next_snapshot_name([s["name"] for s in snapshots]),
        "description_max": DESCRIPTION_MAX,
        "operation": operation,
        "active_operations": active_operations(name),
        "snapshot_message": request.query_params.get("snapshot_message", ""),
        "snapshot_error": request.query_params.get("snapshot_error", ""),
        "raw_list": run_cmd(["virsh", "snapshot-list", "--domain", name], timeout=20)["stdout"],
    })


def vm_active(state: str) -> bool:
    """Running or paused: libvirt saves the memory of both into the snapshot."""
    return presenters.vm_state(state)["running"] or "paus" in (state or "").lower()


_create_lock = threading.Lock()


@router.post("/vm/{name}/snapshots/create")
def snapshot_create(request: Request, name: str, description: str = Form("")):
    with _create_lock:  # two quick clicks must not both pass the checks below
        return _snapshot_create(request, name, description)


def _snapshot_create(request: Request, name: str, description: str):
    blocked, dominfo = _guard(request, name)
    if blocked:
        return blocked
    snapshots = list_snapshots(name)
    blockers = create_blockers(name, snapshots, domain_checks(name))
    if blockers:
        return redirect_with_message(f"/vm/{name}/snapshots", "snapshot_error", blockers[0])
    running = vm_active(presenters.parse_dominfo(dominfo)["state"])
    snap = next_snapshot_name([s["name"] for s in snapshots])
    if not valid_label(snap):
        return redirect_with_message(f"/vm/{name}/snapshots", "snapshot_error", "Не удалось подобрать имя снимка.")
    operation = new_operation("snapshot_create", f"Снимок машины {name}", vm_name=name, snapshot=snap, description=clean_description(description), vm_running=running)
    run_operation(operation, create_worker)
    return RedirectResponse(url=f"/vm/{name}/snapshots?op={operation['id']}", status_code=303)


@router.post("/vm/{name}/snapshots/{snap}/revert")
def snapshot_revert(request: Request, name: str, snap: str):
    blocked, _ = _guard(request, name)
    if blocked:
        return blocked
    if not valid_label(snap):
        return redirect_with_message(f"/vm/{name}/snapshots", "snapshot_error", "Этот снимок создан вне панели и имеет необычное имя — управляйте им через virsh.")
    if active_operations(name):
        return redirect_with_message(f"/vm/{name}/snapshots", "snapshot_error", "Дождитесь окончания предыдущей операции со снимками этой машины.")
    operation = new_operation("snapshot_revert", f"Возврат машины {name} к снимку {snap}", vm_name=name, snapshot=snap)
    run_operation(operation, revert_worker)
    return RedirectResponse(url=f"/vm/{name}/snapshots?op={operation['id']}", status_code=303)


@router.post("/vm/{name}/snapshots/{snap}/delete")
def snapshot_delete(request: Request, name: str, snap: str):
    blocked, _ = _guard(request, name)
    if blocked:
        return blocked
    if not valid_label(snap):
        return redirect_with_message(f"/vm/{name}/snapshots", "snapshot_error", "Этот снимок создан вне панели и имеет необычное имя — управляйте им через virsh.")
    if active_operations(name):
        return redirect_with_message(f"/vm/{name}/snapshots", "snapshot_error", "Дождитесь окончания предыдущей операции со снимками этой машины.")
    result = run_cmd(["virsh", "snapshot-delete", "--domain", name, "--snapshotname", snap], timeout=180)
    if not result["ok"]:
        return redirect_with_message(f"/vm/{name}/snapshots", "snapshot_error", "Не удалось удалить снимок: " + explain_error(cmd_error(result, "virsh завершился с ошибкой")))
    return redirect_with_message(f"/vm/{name}/snapshots", "snapshot_message", f"Снимок «{snap}» удалён")
