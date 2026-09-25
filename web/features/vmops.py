"""Повседневные операции с виртуальной машиной, которых ждут от «простого
Proxmox»: увеличение диска, клонирование, пауза и спящий режим, заметки
(название и описание) и живая нагрузка процессора и памяти.

Всё, что занимает больше пары секунд (клонирование, спящий режим), идёт как
фоновая операция с прогрессом и журналом на странице /operations/{id}.
"""
import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

import core
import presenters
from core import (
    IMAGES_POOL,
    append_operation_log,
    cmd_error,
    finish_operation,
    new_operation,
    redirect_with_message,
    render,
    require_api_auth,
    require_auth,
    run_operation,
    running_operations,
    templates,
    update_operation,
    valid_vm_name,
    vm_exists,
    vm_state,
)

router = APIRouter()

GIB = 1024 ** 3
MAX_DISK_GB = 16 * 1024  # 16 ТБ — потолок формы, чтобы опечатка не сделала диск на петабайт
TITLE_MAX = 80
DESCRIPTION_MAX = 2000
CLONE_TIMEOUT = 6 * 3600  # секунд: копия терабайтного диска на HDD идёт часами
HIBERNATE_TIMEOUT = 1800
SHUT_OFF_STATES = ("shut off", "shutoff", "shut-off")
ACTIVE_STATES = ("running", "idle", "blocked")
SAVE_DIR = Path("/var/lib/libvirt/qemu/save")  # сюда libvirt кладёт память «спящих» машин
RESIZABLE_FORMATS = ("qcow2", "raw")

GUEST_GROW_HINT = "Если система ставилась из облачного образа, место внутри появится само. Иначе расширьте раздел в самой системе: в Linux — growpart или GParted, в Windows — «Управление дисками» → «Расширить том»."

# Известные ошибки virsh/qemu-img/virt-clone → понятное объяснение (подстрока без учёта регистра).
KNOWN_ERRORS = [
    ("has snapshots", "У диска есть снимки — сначала удалите снимки машины, затем увеличьте диск."),
    ("no space left", "На диске сервера закончилось место."),
    ("cannot acquire state change lock", "Машина занята другой операцией. Подождите минуту и попробуйте снова."),
    ("timed out during operation", "Машина занята другой операцией. Подождите минуту и попробуйте снова."),
    ("already exists", "Машина или файл с таким именем уже есть."),
    ("is not running", "Машина выключена, поэтому это действие сейчас недоступно."),
    ("not paused", "Машина не приостановлена."),
    ("already active", "Машина уже работает."),
    ("guest agent", "Внутри машины нет гостевого агента, поэтому это действие недоступно."),
    ("permission denied", "Нет прав на файл диска. Проверьте, что диск лежит в хранилище Virtuality."),
]

OPS = {
    "pause": {
        "cmd": ["virsh", "suspend"],
        "states": ACTIVE_STATES,
        "done": "Машина {name} приостановлена: она не тратит процессор, но остаётся в памяти сервера",
        "wrong": "Приостановить можно только работающую машину",
        "fail": "Не удалось приостановить машину",
    },
    "resume": {
        "cmd": ["virsh", "resume"],
        "states": ("paused",),
        "done": "Машина {name} продолжает работу",
        "wrong": "Продолжить можно только приостановленную машину",
        "fail": "Не удалось продолжить работу машины",
    },
    "forget-save": {
        "cmd": ["virsh", "managedsave-remove"],
        "states": SHUT_OFF_STATES,
        "done": "Сохранённое состояние удалено: при запуске машина {name} загрузится заново",
        "wrong": "Сохранённое состояние есть только у выключенной машины",
        "fail": "Не удалось удалить сохранённое состояние",
    },
}


def run(cmd: list[str], timeout: int = 12) -> dict[str, Any]:
    """Все команды идут через core.run_cmd — тесты подменяют его целиком."""
    return core.run_cmd(cmd, timeout=timeout)


def explain_error(raw: str, fallback: str) -> str:
    low = (raw or "").lower()
    for needle, text in KNOWN_ERRORS:
        if needle in low:
            return text
    return raw or fallback


def is_shut_off(state: str) -> bool:
    return (state or "").strip().lower() in SHUT_OFF_STATES


def redirect_tab(name: str, tab: str, key: str, message: str) -> RedirectResponse:
    """Как redirect_with_message, но открывает нужную вкладку страницы машины."""
    return RedirectResponse(url=f"/vm/{name}?{key}={quote(message)}#{tab}", status_code=303)


def safe_next(value: str, name: str) -> str:
    """Куда вернуть пользователя после действия: только обзор или страница машины."""
    return value if value in ("/", f"/vm/{name}") else f"/vm/{name}"


def _guard(request: Request, name: str):
    """Общая проверка для всех маршрутов: вход, имя, существование машины."""
    denied = require_auth(request)
    if denied:
        return denied
    if not valid_vm_name(name):
        return JSONResponse({"ok": False, "error": "Invalid VM name"}, status_code=400)
    if not vm_exists(name):
        return RedirectResponse(url="/", status_code=303)
    return None


# ---------------------------------------------------------------- parsers
def parse_qemu_img_info(text: str) -> dict[str, Any]:
    """`qemu-img info --output=json` → виртуальный и реальный размер, формат."""
    try:
        data = json.loads(text or "")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        return {"virtual_size": int(data.get("virtual-size") or 0), "actual_size": int(data.get("actual-size") or 0), "format": str(data.get("format") or "")}
    except (TypeError, ValueError):
        return {}


def parse_domstats(text: str) -> dict[str, dict[str, str]]:
    """`virsh domstats` → {имя: {поле: значение}}. Машины без статистики дают пустой словарь."""
    stats: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        match = re.fullmatch(r"Domain:\s*'(.+)'", line)
        if match:
            current = match.group(1)
            stats[current] = {}
            continue
        if current is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        stats[current][key.strip()] = value.strip()
    return stats


def parse_desc_output(result: dict[str, Any]) -> str:
    """Вывод `virsh desc`: пусто, если названия/описания нет."""
    if not result.get("ok"):
        return ""
    text = (result.get("stdout") or "").strip()
    if re.match(r"^No (title|description) for domain", text):
        return ""
    return text


def has_managed_save(dominfo: str) -> bool:
    """Строка «Managed save: yes» в `virsh dominfo` — машина в спящем режиме."""
    match = re.search(r"^Managed save:\s*(\S+)", dominfo or "", re.MULTILINE | re.IGNORECASE)
    return bool(match) and match.group(1).lower() in ("yes", "on", "true")


def _int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def compute_stats(stats: dict[str, dict[str, str]], now: float, samples: dict[str, tuple[float, int]]) -> dict[str, dict[str, Any]]:
    """Проценты процессора считаются по разнице cpu.time с прошлым замером той же машины.

    Первый замер даёт cpu_pct=None (нечего сравнивать). Память — balloon.rss
    (реально занятая на сервере), если нет — balloon.current.
    """
    result: dict[str, dict[str, Any]] = {}
    for name, fields in stats.items():
        cpu_time = _int(fields.get("cpu.time"))
        vcpus = _int(fields.get("vcpu.current")) or _int(fields.get("vcpu.maximum")) or 1
        mem_used_kib = _int(fields.get("balloon.rss")) or _int(fields.get("balloon.current"))
        mem_total_kib = _int(fields.get("balloon.maximum")) or _int(fields.get("balloon.current"))
        if not cpu_time and not mem_total_kib:
            samples.pop(name, None)
            continue  # выключенная машина — статистики нет
        cpu_pct = None
        previous = samples.get(name)
        if previous and now > previous[0] and cpu_time >= previous[1]:
            cpu_pct = (cpu_time - previous[1]) / ((now - previous[0]) * 1e9) / vcpus * 100
            cpu_pct = round(min(100.0, max(0.0, cpu_pct)), 1)
        samples[name] = (now, cpu_time)
        result[name] = {"cpu_pct": cpu_pct, "mem_used_mb": mem_used_kib // 1024, "mem_total_mb": mem_total_kib // 1024, "vcpus": vcpus}
    for stale in set(samples) - set(stats):
        samples.pop(stale, None)
    return result


# ---------------------------------------------------------------- live stats
_samples: dict[str, tuple[float, int]] = {}
_samples_lock = threading.Lock()


def vm_stats(names: list[str]) -> dict[str, dict[str, Any]]:
    """Нагрузка нескольких машин одним вызовом `virsh domstats`."""
    if not names:
        return {}
    result = run(["virsh", "domstats", "--cpu-total", "--balloon", "--vcpu", *names], timeout=10)
    if not result["ok"]:
        return {}
    with _samples_lock:
        return compute_stats(parse_domstats(result["stdout"]), time.monotonic(), _samples)


def active_vm_names() -> list[str]:
    """Имена работающих и приостановленных машин (`virsh list --name`)."""
    result = run(["virsh", "list", "--name"], timeout=8)
    if not result["ok"]:
        return []
    return [line.strip() for line in result["stdout"].splitlines() if valid_vm_name(line.strip())]


def all_vm_names() -> list[str]:
    result = run(["virsh", "list", "--all", "--name"], timeout=8)
    if not result["ok"]:
        return []
    return [line.strip() for line in result["stdout"].splitlines() if line.strip()]


@router.get("/live/stats")
def live_stats(request: Request):
    denied = require_api_auth(request)
    if denied:
        return denied
    return JSONResponse({"ok": True, "generated_at": core.utc_now(), "stats": vm_stats(active_vm_names())})


# ---------------------------------------------------------------- titles & notes
def vm_titles() -> dict[str, str]:
    """Названия всех машин одним вызовом — для списка на обзоре."""
    result = run(["virsh", "list", "--all", "--title"], timeout=10)
    return presenters.parse_virsh_list_titles(result["stdout"]) if result["ok"] else {}


def read_notes(name: str) -> dict[str, str]:
    """Название и описание машины. Без флагов virsh отдаёт текущее значение:
    у работающей машины — живое, у выключенной — из конфигурации."""
    title = run(["virsh", "-q", "desc", name, "--title"], timeout=8)
    description = run(["virsh", "-q", "desc", name], timeout=8)
    return {"title": parse_desc_output(title), "description": parse_desc_output(description)}


def clean_title(value: str) -> str:
    return " ".join((value or "").split())[:TITLE_MAX].strip()


def clean_description(value: str) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()[:DESCRIPTION_MAX].strip()


def write_notes(name: str, title: str, description: str) -> tuple[bool, str]:
    """`virsh desc` в конфигурацию, а у работающей машины — ещё и вживую.
    Текст передаётся как --new-desc=ТЕКСТ одним аргументом: так virsh не
    примет описание вида «--edit» за свою опцию."""
    flags = ["--config"] + (["--live"] if not is_shut_off(vm_state(name)) else [])
    for extra, value in ((["--title"], title), ([], description)):
        result = run(["virsh", "desc", name, *extra, f"--new-desc={value}", *flags], timeout=10)
        if not result["ok"]:
            return False, explain_error(cmd_error(result, "virsh desc завершился с ошибкой"), "Не удалось сохранить заметки")
    return True, "Заметки сохранены"


@router.post("/vm/{name}/notes/save")
def notes_save(request: Request, name: str, title: str = Form(""), description: str = Form("")):
    denied = _guard(request, name)
    if denied:
        return denied
    ok, message = write_notes(name, clean_title(title), clean_description(description))
    return redirect_with_message(f"/vm/{name}", "notes_message" if ok else "notes_error", message)


# ---------------------------------------------------------------- disks
def inside_storage(path: str) -> bool:
    """Диск должен лежать в /var/lib/virtuality — чужие файлы панель не трогает."""
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    return Path(core.STORAGE_DIR).resolve() in resolved.parents


def free_space(path: str | Path) -> int:
    """Свободное место рядом с файлом диска (или в хранилище образов)."""
    for candidate in (Path(path).parent, Path(core.IMAGES_DIR), Path("/")):
        try:
            return shutil.disk_usage(candidate).free
        except OSError:
            continue
    return 0


def disk_info(path: str) -> dict[str, Any]:
    # -U (--force-share): иначе qemu-img не откроет диск работающей машины из-за блокировки.
    result = run(["qemu-img", "info", "--output=json", "-U", path], timeout=20)
    return parse_qemu_img_info(result["stdout"]) if result["ok"] else {}


def vm_disks(name: str) -> list[dict[str, Any]]:
    """Диски машины с размерами; у подходящих — флаг resizable и минимальный новый размер."""
    result = run(["virsh", "domblklist", name, "--details"], timeout=10)
    disks = presenters.parse_domblklist(result["stdout"]) if result["ok"] else []
    items = []
    for disk in disks:
        item: dict[str, Any] = dict(disk, virtual_size=0, actual_size=0, format="", size_text="", used_text="", resizable=False, min_gb=0)
        if disk["device"] == "disk" and disk["source"] and disk["type"] == "file":
            info = disk_info(disk["source"])
            virtual, actual = info.get("virtual_size", 0), info.get("actual_size", 0)
            item.update(virtual_size=virtual, actual_size=actual, format=info.get("format", ""))
            if virtual:
                item["size_text"] = presenters.format_bytes(virtual)
                item["used_text"] = presenters.format_bytes(actual) if actual else ""
                item["min_gb"] = virtual // GIB + 1
                item["resizable"] = item["format"] in RESIZABLE_FORMATS and inside_storage(disk["source"])
        items.append(item)
    return items


def disks_context(name: str) -> dict[str, Any]:
    """Данные для карточки «Диски» на странице машины (вызывается из шаблона)."""
    disks = vm_disks(name)
    first = next((d["source"] for d in disks if d["source"]), None)
    return {"disks": disks, "free_text": presenters.format_bytes(free_space(first or core.IMAGES_DIR)), "max_gb": MAX_DISK_GB, "hint": GUEST_GROW_HINT}


@router.post("/vm/{name}/disks/{target}/resize")
def disk_resize(request: Request, name: str, target: str, size_gb: int = Form(...)):
    denied = _guard(request, name)
    if denied:
        return denied
    if not re.fullmatch(r"[a-z0-9]{1,16}", target or ""):
        return JSONResponse({"ok": False, "error": "Invalid disk target"}, status_code=400)
    disk = next((d for d in vm_disks(name) if d["target"] == target), None)
    if not disk:
        return redirect_tab(name, "storage", "disk_error", "Такого диска у машины нет")
    if not disk["resizable"]:
        return redirect_tab(name, "storage", "disk_error", f"Диск {disk['name']} нельзя увеличить из панели: он не в хранилище Virtuality или в необычном формате")
    if size_gb < disk["min_gb"]:
        return redirect_tab(name, "storage", "disk_error", f"Диск можно только увеличивать: укажите не меньше {disk['min_gb']} ГБ")
    if size_gb > MAX_DISK_GB:
        return redirect_tab(name, "storage", "disk_error", f"Слишком большой размер: не больше {MAX_DISK_GB // 1024} ТБ")
    growth = size_gb * GIB - disk["virtual_size"]
    free = free_space(disk["source"])
    if growth > free:
        return redirect_tab(name, "storage", "disk_error", f"На сервере свободно только {presenters.format_bytes(free)}: столько добавить диску не получится")
    if is_shut_off(vm_state(name)):
        result = run(["qemu-img", "resize", disk["source"], f"{size_gb}G"], timeout=120)
    else:
        result = run(["virsh", "blockresize", name, target, f"{size_gb}G"], timeout=60)
    if not result["ok"]:
        return redirect_tab(name, "storage", "disk_error", explain_error(cmd_error(result, "Не удалось увеличить диск"), "Не удалось увеличить диск"))
    return redirect_tab(name, "storage", "disk_message", f"Диск {disk['name']} увеличен до {size_gb} ГБ. {GUEST_GROW_HINT}")


# ---------------------------------------------------------------- clone
def suggest_clone_name(name: str, existing: set[str]) -> str:
    base = f"{name}-copy"[:60]
    candidate, index = base, 2
    while candidate in existing:
        candidate = f"{base}{index}"
        index += 1
    return candidate


def clone_targets(new_name: str, disks: list[dict[str, Any]]) -> list[Path]:
    """Файлы дисков копии: DST.qcow2, DST-disk2.qcow2, … в порядке domblklist."""
    targets = []
    cloneable = [d for d in disks if d["device"] == "disk" and d["source"]]
    for index, disk in enumerate(cloneable):
        ext = Path(disk["source"]).suffix.lower()
        ext = ext if ext in (".qcow2", ".img", ".raw") else ".qcow2"
        suffix = "" if index == 0 else f"-disk{index + 1}"
        targets.append(Path(core.IMAGES_DIR) / f"{new_name}{suffix}{ext}")
    return targets


def clone_context(name: str) -> dict[str, Any]:
    state = vm_state(name)
    disks = vm_disks(name)
    cloneable = [d for d in disks if d["device"] == "disk" and d["source"]]
    needed = sum(d["actual_size"] or d["virtual_size"] for d in cloneable)
    free = free_space(core.IMAGES_DIR)
    existing = set(all_vm_names())
    blockers = []
    if not is_shut_off(state):
        blockers.append("running")
    if not cloneable:
        blockers.append("no_disks")
    if needed > free:
        blockers.append("space")
    if any(op.get("source_vm") == name for op in running_operations("vm_clone")):
        blockers.append("busy")
    return {
        "state": state, "is_shut_off": is_shut_off(state), "disks": cloneable, "needed_text": presenters.format_bytes(needed),
        "free_text": presenters.format_bytes(free), "suggested": suggest_clone_name(name, existing), "blockers": blockers,
        "vcpus": 0, "memory_mb": 0,
    }


def _allocated_bytes(path: Path) -> int:
    try:
        stat = path.stat()
    except OSError:
        return 0
    blocks = getattr(stat, "st_blocks", None)
    return blocks * 512 if blocks is not None else stat.st_size


def _watch_clone_progress(operation: dict[str, Any], targets: list[Path], expected: int, stop: threading.Event) -> None:
    """Пока virt-clone копирует, следим за ростом файлов копии и обновляем прогресс."""
    while not stop.wait(1.0):
        copied = sum(_allocated_bytes(target) for target in targets)
        if expected > 0:
            pct = min(95, max(3, int(copied * 95 / expected)))
            update_operation(operation, progress=pct, message=f"Копируем диски: {presenters.format_bytes(copied)} из {presenters.format_bytes(expected)}")


def clone_worker(operation: dict[str, Any]) -> None:
    source, new_name = operation["source_vm"], operation["vm_name"]
    targets = [Path(item) for item in operation.get("targets", [])]
    cmd = ["virt-clone", "--original", source, "--name", new_name]
    for target in targets:
        cmd += ["--file", str(target)]
    update_operation(operation, progress=3, message=f"Копируем диски машины {source}…", cmd=" ".join(cmd))
    append_operation_log(operation["id"], " ".join(cmd))
    stop = threading.Event()
    watcher = threading.Thread(target=_watch_clone_progress, args=(operation, targets, int(operation.get("expected_bytes") or 0), stop), daemon=True)
    watcher.start()
    try:
        result = run(cmd, timeout=CLONE_TIMEOUT)
    finally:
        stop.set()
        watcher.join(timeout=5)
    for stream in ("stdout", "stderr"):
        if result.get(stream):
            append_operation_log(operation["id"], result[stream])
    if not result["ok"]:
        for target in targets:  # virt-clone обычно сам убирает недокопированные файлы, но подстрахуемся
            try:
                if target.exists() and not run(["virsh", "dominfo", new_name], timeout=8)["ok"]:
                    target.unlink()
            except OSError:
                pass
        finish_operation(operation, False, explain_error(cmd_error(result, "virt-clone завершился с ошибкой"), "Не удалось клонировать машину"))
        return
    update_operation(operation, progress=97, message="Обновляем список дисков…")
    refresh = run(["virsh", "pool-refresh", IMAGES_POOL], timeout=60)
    if not refresh["ok"]:
        append_operation_log(operation["id"], f"pool-refresh: {cmd_error(refresh, 'не удалось обновить хранилище (не страшно)')}")
    finish_operation(operation, True, f"Машина {new_name} создана — это копия {source}. Запустите её и смените имя компьютера внутри системы.")


@router.get("/vm/{name}/clone")
def clone_page(request: Request, name: str):
    denied = _guard(request, name)
    if denied:
        return denied
    context = clone_context(name)
    context.update(vm_name=name, error=request.query_params.get("clone_error", ""), new_name=request.query_params.get("new_name", "") or context["suggested"])
    return render(request, "vm_clone.html", context)


@router.post("/vm/{name}/clone/start")
def clone_vm(request: Request, name: str, new_name: str = Form("")):
    denied = _guard(request, name)
    if denied:
        return denied
    new_name = (new_name or "").strip()

    def fail(message: str):
        return RedirectResponse(url=f"/vm/{name}/clone?clone_error={quote(message)}&new_name={quote(new_name)}", status_code=303)

    if not valid_vm_name(new_name):
        return fail("Имя копии: от 2 до 63 символов — латинские буквы, цифры, точка, дефис и подчёркивание, первый символ — буква или цифра")
    if new_name.lower() == name.lower() or new_name in set(all_vm_names()) or vm_exists(new_name):
        return fail(f"Машина с именем {new_name} уже есть — выберите другое имя")
    context = clone_context(name)
    if "running" in context["blockers"]:
        return fail("Сначала выключите машину: копировать диски можно только у выключенной машины")
    if "no_disks" in context["blockers"]:
        return fail("У машины нет дисков, которые можно скопировать")
    if "busy" in context["blockers"]:
        return fail("Эта машина уже клонируется — дождитесь окончания в разделе «Задачи»")
    if any(op.get("vm_name") == new_name for op in running_operations("vm_clone")):
        return fail(f"Копия с именем {new_name} уже создаётся — выберите другое имя")
    if "space" in context["blockers"]:
        return fail(f"Не хватает места: копии нужно {context['needed_text']}, свободно {context['free_text']}")
    targets = clone_targets(new_name, context["disks"])
    for target in targets:
        if target.exists():
            return fail(f"Файл {target.name} уже есть в хранилище — выберите другое имя")
    needed = sum(d["actual_size"] or d["virtual_size"] for d in context["disks"])
    operation = new_operation("vm_clone", f"Клонирование {name} → {new_name}", vm_name=new_name, source_vm=name, targets=[str(t) for t in targets], expected_bytes=needed)
    run_operation(operation, clone_worker)
    return RedirectResponse(url=f"/operations/{operation['id']}", status_code=303)


# ---------------------------------------------------------------- pause / resume / hibernate
def hibernate_worker(operation: dict[str, Any]) -> None:
    name = operation["vm_name"]
    update_operation(operation, progress=10, message="Сохраняем память машины на диск сервера…")
    result = run(["virsh", "managedsave", name], timeout=HIBERNATE_TIMEOUT)
    for stream in ("stdout", "stderr"):
        if result.get(stream):
            append_operation_log(operation["id"], result[stream])
    if not result["ok"]:
        finish_operation(operation, False, explain_error(cmd_error(result, "virsh managedsave завершился с ошибкой"), "Не удалось перевести машину в спящий режим"))
        return
    finish_operation(operation, True, f"Машина {name} в спящем режиме. При запуске она продолжит работу с того же места.")


def hibernate(name: str, state: str, target: str):
    if state not in ACTIVE_STATES + ("paused",):
        return redirect_with_message(target, "ops_error", "В спящий режим можно перевести только работающую или приостановленную машину")
    if running_operations("vm_hibernate"):
        return redirect_with_message(target, "ops_error", "Другая машина сейчас засыпает — подождите минуту")
    info = presenters.parse_dominfo(run(["virsh", "dominfo", name], timeout=8).get("stdout", ""))
    needed = info["memory_mb"] * 1024 * 1024
    if needed and needed > free_space(SAVE_DIR / "x"):
        return redirect_with_message(target, "ops_error", f"Для спящего режима нужно {presenters.format_bytes(needed)} свободного места на сервере, а его нет")
    operation = new_operation("vm_hibernate", f"Спящий режим: {name}", vm_name=name)
    run_operation(operation, hibernate_worker)
    return redirect_with_message(target, "ops_message", f"Машина {name} засыпает: состояние сохраняется на диск, это займёт до минуты")


@router.post("/vm/{name}/ops/{action}")
def vm_ops(request: Request, name: str, action: str, next: str = Form("")):
    denied = require_auth(request)
    if denied:
        return denied
    if not valid_vm_name(name):
        return JSONResponse({"ok": False, "error": "Invalid VM name"}, status_code=400)
    if action != "hibernate" and action not in OPS:
        return JSONResponse({"ok": False, "error": "Unsupported action"}, status_code=400)
    if not vm_exists(name):
        return RedirectResponse(url="/", status_code=303)
    target = safe_next(next, name)
    state = vm_state(name)
    if action == "hibernate":
        return hibernate(name, state, target)
    spec = OPS[action]
    if state not in spec["states"]:
        return redirect_with_message(target, "ops_error", spec["wrong"])
    result = run(spec["cmd"] + [name], timeout=30)
    if not result["ok"]:
        return redirect_with_message(target, "ops_error", explain_error(cmd_error(result, spec["fail"]), spec["fail"]))
    return redirect_with_message(target, "ops_message", spec["done"].format(name=name))


# Шаблоны берут данные напрямую (как update_notice() в base.html), чтобы
# страница машины и обзор в app.py не менялись.
TEMPLATE_GLOBALS = {"vmops_disks": disks_context, "vmops_notes": read_notes, "vmops_titles": vm_titles, "vmops_saved": has_managed_save}
templates.env.globals.update(TEMPLATE_GLOBALS)

# Обзор и страница машины пока рендерятся отдельным окружением шаблонов из app.py —
# подключаем и его. Циклический импорт безопасен: app.py импортирует этот модуль
# последними строками, когда templates уже создан.
import app as _app  # noqa: E402

if getattr(_app, "templates", None) is not None and _app.templates is not templates:
    _app.templates.env.globals.update(TEMPLATE_GLOBALS)
