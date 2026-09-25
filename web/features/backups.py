"""Резервные копии виртуальных машин.

Копия лежит в BACKUPS_DIR/<машина>/<ГГГГММДД-ЧЧММ>/ и состоит из vm.xml
(настройки), <диск>.qcow2 на каждый диск и meta.json. Создание и
восстановление выполняются как фоновые операции с прогрессом и журналом.

`create_backup(vm_name, note)` можно вызывать без HTTP — например, из
расписания: `python3 web/features/backups.py web01 "ночная копия"`.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

if __name__ == "__main__":  # запуск как скрипта: нужен web/ в sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import APIRouter, Form, Request  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402

import core  # noqa: E402
import presenters  # noqa: E402

router = APIRouter()

BACKUP_ID_RE = re.compile(r"\d{8}-\d{4}")
SHUTDOWN_TIMEOUT = 180.0  # секунд ждём мягкого выключения
POLL_INTERVAL = 2.0
SPACE_MARGIN = 256 * 1024 * 1024  # запас, чтобы не забить диск под завязку
NOTE_MAX = 120
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


class BackupError(Exception):
    """Ошибка, текст которой можно показать пользователю как есть."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- helpers
def run(cmd: list[str], timeout: int = 12) -> dict[str, Any]:
    return core.run_cmd(cmd, timeout=timeout)


def stream_cmd(cmd: list[str], on_line: Callable[[str], None]) -> int:
    """Запустить команду и отдавать каждую строку вывода (по \\r и \\n) в on_line.

    qemu-img с ключом -p печатает прогресс через \\r, поэтому обычное чтение
    по строкам не подходит. Возвращает код завершения."""
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    pending = b""
    assert process.stdout is not None
    while True:
        chunk = process.stdout.read(512)
        if not chunk:
            break
        pending += chunk
        parts = re.split(rb"[\r\n]+", pending)
        pending = parts.pop()
        for part in parts:
            text = part.decode("utf-8", "replace").strip()
            if text:
                on_line(text)
    tail = pending.decode("utf-8", "replace").strip()
    if tail:
        on_line(tail)
    return process.wait()


def parse_progress(line: str) -> int | None:
    """'    (12.34/100%)' → 12. None, если в строке нет прогресса qemu-img."""
    match = re.search(r"\((\d+(?:\.\d+)?)/100%\)", line or "")
    return min(100, int(float(match.group(1)))) if match else None


def parse_qemu_img_info(text: str) -> dict[str, Any]:
    """Вывод `qemu-img info --output=json` → {virtual_size, actual_size, format}."""
    empty = {"virtual_size": 0, "actual_size": 0, "format": ""}
    try:
        data = json.loads(text or "")
    except ValueError:
        return empty
    if not isinstance(data, dict):
        return empty
    try:
        return {"virtual_size": int(data.get("virtual-size") or 0), "actual_size": int(data.get("actual-size") or 0), "format": str(data.get("format") or "")}
    except (TypeError, ValueError):
        return empty


def disk_info(path: str) -> dict[str, Any]:
    result = run(["qemu-img", "info", "--output=json", path], timeout=30)
    return parse_qemu_img_info(result["stdout"]) if result["ok"] else parse_qemu_img_info("")


def vm_disks(name: str) -> list[dict[str, str]]:
    """Диски машины (без приводов CD/DVD) из `virsh domblklist --details`."""
    result = run(["virsh", "domblklist", name, "--details"], timeout=10)
    if not result["ok"]:
        raise BackupError("Не удалось получить список дисков: " + core.cmd_error(result, "virsh domblklist"))
    return [disk for disk in presenters.parse_domblklist(result["stdout"]) if disk["device"] == "disk" and disk["source"]]


def vm_cdroms_with_media(name: str) -> list[dict[str, str]]:
    result = run(["virsh", "domblklist", name, "--details"], timeout=10)
    if not result["ok"]:
        return []
    return [disk for disk in presenters.parse_domblklist(result["stdout"]) if disk["device"] == "cdrom" and disk["source"]]


def free_bytes(path: Path) -> int:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        return shutil.disk_usage(target).free
    except OSError:
        return 0


def format_bytes(value: int | float) -> str:
    return presenters.format_bytes(value)


def format_backup_date(backup_id: str) -> str:
    try:
        moment = datetime.strptime(backup_id, "%Y%m%d-%H%M")
    except ValueError:
        return backup_id
    return f"{moment.day} {MONTHS[moment.month - 1]} {moment.year}, {moment:%H:%M}"


def clean_note(note: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", note or "").strip()[:NOTE_MAX]


def existing_vm_names() -> set[str]:
    result = run(["virsh", "list", "--all", "--name"], timeout=10)
    return {line.strip() for line in (result.get("stdout") or "").splitlines() if line.strip()} if result["ok"] else set()


# ---------------------------------------------------------------- storage layout
def backup_root() -> Path:
    return core.BACKUPS_DIR


def backup_path(vm: str, backup_id: str) -> Path | None:
    """Папка копии или None, если имя машины/копии некорректно или уводит из BACKUPS_DIR."""
    if not core.valid_vm_name(vm) or not BACKUP_ID_RE.fullmatch(backup_id or ""):
        return None
    root = backup_root()
    path = root / vm / backup_id
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return path


def describe_backup(vm: str, backup_id: str, path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    disks = [disk for disk in meta.get("disks", []) if isinstance(disk, dict) and disk.get("target")]
    size = int(meta.get("size_bytes") or 0)
    complete = (path / "vm.xml").exists() and bool(disks) and all((path / f"{disk['target']}.qcow2").exists() for disk in disks)
    return {
        "vm": vm,
        "id": backup_id,
        "path": str(path),
        "created_at": meta.get("created_at") or "",
        "created_label": format_backup_date(backup_id),
        "note": clean_note(str(meta.get("note") or "")),
        "disks": disks,
        "disk_count": len(disks),
        "size_bytes": size,
        "size": format_bytes(size),
        "version": str(meta.get("version") or ""),
        "complete": complete,
    }


def read_backup(vm: str, backup_id: str) -> dict[str, Any] | None:
    path = backup_path(vm, backup_id)
    if not path or not (path / "meta.json").is_file():
        return None
    try:
        meta = json.loads((path / "meta.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    return describe_backup(vm, backup_id, path, meta)


def list_backups(vm: str | None = None) -> list[dict[str, Any]]:
    """Все копии (или копии одной машины), новые сверху."""
    root = backup_root()
    if not root.is_dir():
        return []
    if vm is not None:
        vm_dirs = [root / vm] if core.valid_vm_name(vm) else []
    else:
        vm_dirs = sorted(item for item in root.iterdir() if item.is_dir() and core.valid_vm_name(item.name))
    items: list[dict[str, Any]] = []
    for vm_dir in vm_dirs:
        if not vm_dir.is_dir():
            continue
        for item in vm_dir.iterdir():
            if item.is_dir() and BACKUP_ID_RE.fullmatch(item.name):
                backup = read_backup(vm_dir.name, item.name)
                if backup:
                    items.append(backup)
    items.sort(key=lambda item: (item["id"], item["vm"]), reverse=True)
    return items


def group_by_vm(items: list[dict[str, Any]], existing: set[str] | None = None) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        group = groups.setdefault(item["vm"], {"vm": item["vm"], "backups": [], "size_bytes": 0, "exists": existing is None or item["vm"] in existing})
        group["backups"].append(item)
        group["size_bytes"] += item["size_bytes"]
    for group in groups.values():
        group["size"] = format_bytes(group["size_bytes"])
        group["count"] = len(group["backups"])
    return sorted(groups.values(), key=lambda group: group["vm"])


def storage_summary(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = list_backups() if items is None else items
    used = sum(item["size_bytes"] for item in items)
    free = free_bytes(backup_root())
    total = used + free
    return {
        "count": len(items),
        "used_bytes": used,
        "used": format_bytes(used),
        "free_bytes": free,
        "free": format_bytes(free),
        "used_pct": int(round(used / total * 100)) if total else 0,
    }


def remove_backup_dir(path: Path) -> None:
    """Удалить папку копии; пустую папку машины над ней — тоже."""
    shutil.rmtree(path, ignore_errors=True)
    parent = path.parent
    try:
        if parent != backup_root() and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def delete_backup(vm: str, backup_id: str) -> None:
    path = backup_path(vm, backup_id)
    if not path or not path.is_dir():
        raise BackupError("Копия не найдена — возможно, её уже удалили")
    for operation in core.running_operations():
        if operation.get("type") in ("backup", "restore") and operation.get("backup_id") == backup_id and operation.get("source_vm", operation.get("vm_name")) == vm:
            raise BackupError("С этой копией сейчас идёт работа — дождитесь завершения задачи")
    remove_backup_dir(path)


def active_operation(vm: str) -> dict[str, Any] | None:
    """Идущая сейчас задача копирования/восстановления, связанная с машиной."""
    for operation in core.running_operations():
        if operation.get("type") in ("backup", "restore") and vm in (operation.get("vm_name"), operation.get("source_vm")):
            return operation
    return None


# ---------------------------------------------------------------- создание копии
def create_backup(vm_name: str, note: str = "", wait: bool = False) -> dict[str, Any]:
    """Поставить создание копии в очередь и вернуть операцию.

    Проверки (машина есть, диски есть, места хватает) выполняются сразу и
    поднимают BackupError с понятным текстом. С wait=True копия делается
    в текущем потоке — удобно для расписания."""
    if not core.valid_vm_name(vm_name):
        raise BackupError("Некорректное имя машины")
    note = clean_note(note)
    if not core.vm_exists(vm_name):
        raise BackupError(f"Машина {vm_name} не найдена")
    if active_operation(vm_name):
        raise BackupError("С этой машиной уже идёт копирование или восстановление — дождитесь завершения")
    disks = vm_disks(vm_name)
    if not disks:
        raise BackupError("У машины нет дисков — копировать нечего")
    plan: list[dict[str, Any]] = []
    need = 0
    for disk in disks:
        info = disk_info(disk["source"])
        size = info["actual_size"] or info["virtual_size"]
        need += size
        plan.append({"target": disk["target"], "source": disk["source"], "size": size})
    free = free_bytes(backup_root())
    if free < need + SPACE_MARGIN:
        raise BackupError(f"Недостаточно места для копии: нужно около {format_bytes(need)}, свободно {format_bytes(free)}. Удалите старые копии или освободите место на сервере.", "space")
    backup_id = datetime.now().strftime("%Y%m%d-%H%M")
    if (backup_root() / vm_name / backup_id).exists():
        raise BackupError("Копия этой машины уже создана в эту минуту. Подождите минуту и повторите.")
    operation = core.new_operation("backup", f"Резервная копия {vm_name}", "Ожидает начала", vm_name=vm_name, backup_id=backup_id, note=note, disks=plan, size_bytes=need)
    if wait:
        core.update_operation(operation, status="running", started_at=core.utc_now())
        backup_worker(operation)
        return core.read_operation(operation["id"]) or operation
    core.run_operation(operation, backup_worker)
    return operation


def disk_progress_reporter(operation: dict[str, Any], index: int, total: int, label: str) -> Callable[[str], None]:
    """on_line для qemu-img: общий прогресс = 5…95 %, разложенный по дискам."""
    state = {"last": -1, "logged": -10}

    def on_line(line: str) -> None:
        pct = parse_progress(line)
        if pct is None:
            core.append_operation_log(operation["id"], line)
            return
        overall = int(5 + (index + pct / 100) / total * 90)
        if overall == state["last"]:
            return
        state["last"] = overall
        core.update_operation(operation, progress=overall, message=f"{label}: {pct}%")
        if pct - state["logged"] >= 10 or pct == 100:
            state["logged"] = pct
            core.append_operation_log(operation["id"], f"{label}: {pct}%")

    return on_line


def shutdown_and_wait(operation: dict[str, Any], vm: str) -> bool:
    log = lambda text: core.append_operation_log(operation["id"], text)  # noqa: E731
    log("Машина работает — отправляем команду выключения (как кнопка питания, система завершит работу сама).")
    core.update_operation(operation, progress=2, message="Выключаем машину…")
    result = run(["virsh", "shutdown", vm], timeout=20)
    if not result["ok"]:
        log("Команда выключения не принята: " + core.cmd_error(result, "virsh shutdown"))
        return False
    deadline = time.monotonic() + SHUTDOWN_TIMEOUT
    polls = 0
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        polls += 1
        if not core.vm_is_running(vm):
            log("Машина выключена.")
            return True
        if polls % 5 == 0:
            waited = int(polls * POLL_INTERVAL)
            core.update_operation(operation, message=f"Ждём выключения машины… {waited} с из {int(SHUTDOWN_TIMEOUT)}")
            log(f"Всё ещё работает, ждём ({waited} с).")
    return False


def start_vm(operation: dict[str, Any], vm: str) -> str:
    """Запустить машину обратно; вернуть текст проблемы или пустую строку."""
    result = run(["virsh", "start", vm], timeout=40)
    if result["ok"]:
        core.append_operation_log(operation["id"], "Машина запущена снова.")
        return ""
    problem = core.cmd_error(result, "virsh start")
    core.append_operation_log(operation["id"], "Не удалось запустить машину: " + problem)
    return problem


def backup_worker(operation: dict[str, Any]) -> None:
    vm = operation["vm_name"]
    plan = operation["disks"]
    log = lambda text: core.append_operation_log(operation["id"], text)  # noqa: E731
    was_running = core.vm_is_running(vm)
    if was_running and not shutdown_and_wait(operation, vm):
        core.finish_operation(operation, False, "Машина не выключилась за 3 минуты. Сохраните работу внутри неё, выключите её вручную и создайте копию ещё раз.")
        return
    dest = backup_root() / vm / operation["backup_id"]
    size = 0
    try:
        dest.mkdir(parents=True, exist_ok=False)
        result = run(["virsh", "dumpxml", vm, "--migratable"], timeout=20)
        if not result["ok"]:
            raise BackupError("Не удалось сохранить настройки машины: " + core.cmd_error(result, "virsh dumpxml"))
        (dest / "vm.xml").write_text(result["stdout"].rstrip() + "\n", encoding="utf-8")
        log("Настройки машины сохранены в vm.xml.")
        total = len(plan)
        for index, disk in enumerate(plan):
            target_file = dest / f"{disk['target']}.qcow2"
            label = f"Копируем диск {disk['target']} ({index + 1} из {total})"
            core.update_operation(operation, progress=int(5 + index / total * 90), message=label)
            log(f"{label}: {disk['source']} → {target_file}")
            cmd = ["qemu-img", "convert", "-p", "-O", "qcow2", "-c", disk["source"], str(target_file)]
            log(" ".join(cmd))
            code = stream_cmd(cmd, disk_progress_reporter(operation, index, total, label))
            if code != 0:
                raise BackupError(f"Не удалось скопировать диск {disk['target']} (qemu-img завершился с кодом {code})")
        size = sum(item.stat().st_size for item in dest.iterdir() if item.is_file())
        meta = {"vm": vm, "created_at": core.utc_now(), "disks": plan, "size_bytes": size, "version": core.APP_VERSION, "note": operation.get("note", "")}
        (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"Копия готова: {format_bytes(size)} (сжатие включено).")
    except Exception as exc:  # noqa: BLE001 — любая ошибка = операция с понятным текстом
        remove_backup_dir(dest)
        log(f"Ошибка: {exc}")
        problem = start_vm(operation, vm) if was_running else ""
        message = str(exc)[:200]
        if problem:
            message += f". Машину тоже не удалось запустить: {problem}"
        core.finish_operation(operation, False, message)
        return
    problem = start_vm(operation, vm) if was_running else ""
    message = f"Резервная копия создана: {format_bytes(size)}"
    if problem:
        message += f". Но машину не удалось запустить снова: {problem}"
    core.finish_operation(operation, True, message, size_bytes=size)


# ---------------------------------------------------------------- восстановление
def rewrite_xml(xml_text: str, new_name: str, disk_map: dict[str, str]) -> str:
    """Подготовить XML копии к `virsh define` под именем new_name.

    Меняет <name>, убирает <uuid> и id, переключает диски на новые файлы,
    удаляет <mac> (libvirt выдаст новые адреса) и <nvram> (libvirt заведёт
    свой файл под новое имя, чтобы не делить его со старой машиной)."""
    root = ET.fromstring(xml_text)
    root.attrib.pop("id", None)
    name = root.find("name")
    if name is None:
        name = ET.Element("name")
        root.insert(0, name)
    name.text = new_name
    for element in root.findall("uuid"):
        root.remove(element)
    devices = root.find("devices")
    if devices is not None:
        for disk in devices.findall("disk"):
            target = disk.find("target")
            dev = target.get("dev") if target is not None else None
            if disk.get("device") != "disk" or dev not in disk_map:
                continue
            source = disk.find("source")
            if source is None:
                source = ET.SubElement(disk, "source")
            source.attrib.clear()
            source.set("file", disk_map[dev])
            disk.set("type", "file")
            driver = disk.find("driver")
            if driver is None:
                driver = ET.SubElement(disk, "driver")
                driver.set("name", "qemu")
            driver.set("type", "qcow2")
        for interface in devices.findall("interface"):
            for mac in interface.findall("mac"):
                interface.remove(mac)
    os_element = root.find("os")
    if os_element is not None:
        for nvram in os_element.findall("nvram"):
            os_element.remove(nvram)
    return ET.tostring(root, encoding="unicode")


def restore_disk_path(new_name: str, target: str, index: int) -> Path:
    images = core.IMAGES_DIR
    candidates = [images / f"{new_name}.qcow2"] if index == 0 else []
    candidates.append(images / f"{new_name}-{target}.qcow2")
    for candidate in candidates:
        if not candidate.exists():
            return candidate
    counter = 2
    while (images / f"{new_name}-{target}-{counter}.qcow2").exists():
        counter += 1
    return images / f"{new_name}-{target}-{counter}.qcow2"


def plan_restore(vm: str, backup_id: str, new_name: str, replace: bool) -> dict[str, Any]:
    backup = read_backup(vm, backup_id)
    if not backup:
        raise BackupError("Копия не найдена")
    if not backup["complete"]:
        raise BackupError("Копия неполная — не хватает файлов. Восстановить из неё нельзя.")
    new_name = (new_name or "").strip() or vm
    if not core.valid_vm_name(new_name):
        raise BackupError("Название машины: латинские буквы, цифры, точка, дефис, от 2 до 63 символов", "name")
    exists = core.vm_exists(new_name)
    if exists and not replace:
        raise BackupError(f"Машина {new_name} уже есть на сервере. Укажите другое название или подтвердите замену.", "exists")
    if active_operation(new_name) or active_operation(vm):
        raise BackupError("С этой машиной уже идёт копирование или восстановление — дождитесь завершения")
    need = sum(int(disk.get("size") or 0) for disk in backup["disks"])
    reclaim = 0
    if exists:
        try:
            reclaim = sum(disk_info(disk["source"])["actual_size"] for disk in vm_disks(new_name))
        except BackupError:
            reclaim = 0
    free = free_bytes(core.IMAGES_DIR) + reclaim
    if free < need + SPACE_MARGIN:
        raise BackupError(f"Недостаточно места для дисков машины: нужно около {format_bytes(need)}, свободно {format_bytes(free)}.", "space")
    return {"backup": backup, "new_name": new_name, "replace": exists, "need": need}


def start_restore(vm: str, backup_id: str, new_name: str, replace: bool) -> dict[str, Any]:
    plan = plan_restore(vm, backup_id, new_name, replace)
    backup = plan["backup"]
    title = f"Восстановление {plan['new_name']} из копии"
    operation = core.new_operation("restore", title, "Ожидает начала", vm_name=plan["new_name"], source_vm=vm, backup_id=backup_id, replace=plan["replace"], disks=backup["disks"], size_bytes=plan["need"])
    core.run_operation(operation, restore_worker)
    return operation


def remove_vm(operation: dict[str, Any], name: str) -> None:
    """Удалить существующую машину вместе с дисками перед восстановлением."""
    log = lambda text: core.append_operation_log(operation["id"], text)  # noqa: E731
    if core.vm_is_running(name):
        log(f"Машина {name} работает — выключаем принудительно.")
        result = run(["virsh", "destroy", name], timeout=30)
        if not result["ok"]:
            raise BackupError("Не удалось выключить старую машину: " + core.cmd_error(result, "virsh destroy"))
    for cdrom in vm_cdroms_with_media(name):
        # Иначе `undefine --remove-all-storage` удалит и вставленный ISO-образ.
        result = run(["virsh", "change-media", name, cdrom["target"], "--eject", "--config"], timeout=20)
        if not result["ok"]:
            raise BackupError(f"Не удалось извлечь образ из привода {cdrom['target']}: " + core.cmd_error(result, "virsh change-media"))
        log(f"Образ извлечён из привода {cdrom['target']}, чтобы он не был удалён вместе с машиной.")
    result = run(["virsh", "undefine", name, "--remove-all-storage", "--nvram", "--managed-save", "--snapshots-metadata"], timeout=180)
    if not result["ok"]:
        raise BackupError("Не удалось удалить старую машину: " + core.cmd_error(result, "virsh undefine"))
    log(f"Старая машина {name} удалена вместе с дисками.")


def restore_worker(operation: dict[str, Any]) -> None:
    new_name = operation["vm_name"]
    log = lambda text: core.append_operation_log(operation["id"], text)  # noqa: E731
    backup = read_backup(operation["source_vm"], operation["backup_id"])
    created: list[Path] = []
    try:
        if not backup or not backup["complete"]:
            raise BackupError("Копия не найдена или неполная")
        if operation.get("replace"):
            core.update_operation(operation, progress=3, message=f"Удаляем существующую машину {new_name}…")
            remove_vm(operation, new_name)
        core.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        disk_map: dict[str, str] = {}
        total = len(backup["disks"])
        for index, disk in enumerate(backup["disks"]):
            source = Path(backup["path"]) / f"{disk['target']}.qcow2"
            target = restore_disk_path(new_name, disk["target"], index)
            created.append(target)
            label = f"Восстанавливаем диск {disk['target']} ({index + 1} из {total})"
            core.update_operation(operation, progress=int(5 + index / total * 90), message=label)
            log(f"{label}: {source} → {target}")
            cmd = ["qemu-img", "convert", "-p", "-O", "qcow2", str(source), str(target)]
            log(" ".join(cmd))
            code = stream_cmd(cmd, disk_progress_reporter(operation, index, total, label))
            if code != 0:
                raise BackupError(f"Не удалось восстановить диск {disk['target']} (qemu-img завершился с кодом {code})")
            disk_map[disk["target"]] = str(target)
        core.update_operation(operation, progress=96, message="Регистрируем машину…")
        xml = rewrite_xml((Path(backup["path"]) / "vm.xml").read_text(encoding="utf-8"), new_name, disk_map)
        with tempfile.NamedTemporaryFile("w", dir=backup["path"], prefix="restore-", suffix=".xml", delete=False, encoding="utf-8") as handle:
            handle.write(xml)
            tmp_xml = Path(handle.name)
        try:
            result = run(["virsh", "define", str(tmp_xml)], timeout=30)
        finally:
            tmp_xml.unlink(missing_ok=True)
        if not result["ok"]:
            raise BackupError("Не удалось зарегистрировать машину: " + core.cmd_error(result, "virsh define"))
        run(["virsh", "pool-refresh", core.IMAGES_POOL], timeout=20)
        log(f"Машина {new_name} зарегистрирована.")
    except Exception as exc:  # noqa: BLE001
        for path in created:
            path.unlink(missing_ok=True)
        log(f"Ошибка: {exc}")
        core.finish_operation(operation, False, str(exc)[:240])
        return
    created_label = backup["created_label"] if backup else ""
    core.finish_operation(operation, True, f"Машина {new_name} восстановлена из копии от {created_label}. Она выключена — запустите её, когда будете готовы.")


# ---------------------------------------------------------------- страницы
def page_messages(request: Request) -> dict[str, str]:
    return {"backup_message": request.query_params.get("backup_message", ""), "backup_error": request.query_params.get("backup_error", "")}


def vm_summary(name: str) -> dict[str, Any]:
    dominfo = run(["virsh", "dominfo", name], timeout=10)["stdout"]
    info = presenters.parse_dominfo(dominfo)
    return {"name": name, "info": info, "state": info["state"] or core.vm_state(name)}


@router.get("/backups")
def backups_page(request: Request):
    auth = core.require_auth(request)
    if auth:
        return auth
    items = list_backups()
    context = {
        "groups": group_by_vm(items, existing_vm_names()),
        "storage": storage_summary(items),
        "running": [op for op in core.running_operations() if op.get("type") in ("backup", "restore")],
        **page_messages(request),
    }
    return core.render(request, "backups.html", context)


@router.post("/backups/{vm}/{backup_id}/delete")
def backup_delete(request: Request, vm: str, backup_id: str, back_to: str = Form("", alias="next")):
    auth = core.require_auth(request)
    if auth:
        return auth
    back = back_to if back_to == f"/vm/{vm}/backups" and core.valid_vm_name(vm) else "/backups"
    try:
        delete_backup(vm, backup_id)
    except BackupError as exc:
        return core.redirect_with_message(back, "backup_error", str(exc))
    return core.redirect_with_message(back, "backup_message", f"Копия от {format_backup_date(backup_id)} удалена")


def restore_form(request: Request, backup: dict[str, Any], name: str, exists: bool, error: str = "", status_code: int = 200):
    need = sum(int(disk.get("size") or 0) for disk in backup["disks"])
    context = {"backup": backup, "name": name, "exists": exists, "error": error, "need": format_bytes(need), "free": format_bytes(free_bytes(core.IMAGES_DIR)), "images_dir": str(core.IMAGES_DIR)}
    return core.render(request, "backup_restore.html", context, status_code=status_code)


@router.get("/backups/{vm}/{backup_id}/restore")
def backup_restore_page(request: Request, vm: str, backup_id: str):
    auth = core.require_auth(request)
    if auth:
        return auth
    backup = read_backup(vm, backup_id)
    if not backup:
        return core.redirect_with_message("/backups", "backup_error", "Копия не найдена")
    return restore_form(request, backup, vm, core.vm_exists(vm))


@router.post("/backups/{vm}/{backup_id}/restore")
def backup_restore_submit(request: Request, vm: str, backup_id: str, name: str = Form(""), replace: str = Form("")):
    auth = core.require_auth(request)
    if auth:
        return auth
    backup = read_backup(vm, backup_id)
    if not backup:
        return core.redirect_with_message("/backups", "backup_error", "Копия не найдена")
    name = (name or "").strip() or vm
    try:
        operation = start_restore(vm, backup_id, name, replace == "1")
    except BackupError as exc:
        exists = exc.code == "exists" or (core.valid_vm_name(name) and core.vm_exists(name))
        return restore_form(request, backup, name, exists, str(exc), status_code=400)
    return RedirectResponse(url=f"/operations/{operation['id']}", status_code=303)


@router.get("/vm/{name}/backups")
def vm_backups_page(request: Request, name: str):
    auth = core.require_auth(request)
    if auth:
        return auth
    if not core.valid_vm_name(name) or not core.vm_exists(name):
        return RedirectResponse(url="/", status_code=303)
    items = list_backups(name)
    context = {"vm": vm_summary(name), "backups": items, "storage": storage_summary(), "active": active_operation(name), **page_messages(request)}
    return core.render(request, "vm_backups.html", context)


@router.post("/vm/{name}/backups/create")
def vm_backup_create(request: Request, name: str, note: str = Form("")):
    auth = core.require_auth(request)
    if auth:
        return auth
    if not core.valid_vm_name(name):
        return RedirectResponse(url="/", status_code=303)
    try:
        operation = create_backup(name, note)
    except BackupError as exc:
        return core.redirect_with_message(f"/vm/{name}/backups", "backup_error", str(exc))
    return RedirectResponse(url=f"/operations/{operation['id']}", status_code=303)


if __name__ == "__main__":  # python3 web/features/backups.py <машина> [заметка]
    try:
        finished = create_backup(sys.argv[1], " ".join(sys.argv[2:]), wait=True)
    except (IndexError, BackupError) as error:
        print(f"Ошибка: {error or 'укажите имя машины'}")
        sys.exit(2)
    print(f"{finished['status']}: {finished['message']}")
    sys.exit(0 if finished["status"] == "success" else 1)
