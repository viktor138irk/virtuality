#!/usr/bin/env python3
"""Бэкапы дисков VM: сжатые qcow2-копии в /var/lib/virtuality/backups.

Без внешних зависимостей — модуль используется и веб-панелью, и ночным
systemd-таймером (запуск: python3 backup_core.py --scheduled).
"""
import json
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

BACKUPS_DIR = Path("/var/lib/virtuality/backups")
SCHEDULE_FILE = Path("/var/lib/virtuality/config/backup_schedule.json")
SCHEDULE_LOCK = threading.Lock()
LOG_FILE = Path("/var/log/virtuality/backup.log")
DEFAULT_KEEP = 5


def valid_vm_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,62}", name or ""))


def valid_backup_filename(name: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,120}\.qcow2", name or ""))


def backup_dir_for(vm_name: str) -> Path | None:
    if not valid_vm_name(vm_name):
        return None
    path = (BACKUPS_DIR / vm_name).resolve()
    if BACKUPS_DIR.resolve() not in path.parents:
        return None
    return path


def backup_path(vm_name: str, filename: str) -> Path | None:
    vm_dir = backup_dir_for(vm_name)
    if vm_dir is None or not valid_backup_filename(filename):
        return None
    path = (vm_dir / filename).resolve()
    if vm_dir not in path.parents:
        return None
    return path


def new_backup_path(vm_name: str) -> Path | None:
    vm_dir = backup_dir_for(vm_name)
    if vm_dir is None:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return vm_dir / f"{vm_name}_{stamp}.qcow2"


def list_backups() -> list[dict[str, Any]]:
    backups = []
    if not BACKUPS_DIR.exists():
        return backups
    for vm_dir in sorted(BACKUPS_DIR.iterdir()):
        if not vm_dir.is_dir() or not valid_vm_name(vm_dir.name):
            continue
        for item in sorted(vm_dir.glob("*.qcow2"), reverse=True):
            try:
                stat = item.stat()
            except OSError:
                continue
            backups.append({
                "vm": vm_dir.name,
                "name": item.name,
                "path": str(item),
                "size": f"{stat.st_size / 1024 / 1024:.0f} MB",
                "created": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    return backups


def rotate_backups(vm_name: str, keep: int) -> list[str]:
    """Удаляет старые бэкапы сверх keep. Возвращает имена удалённых файлов."""
    vm_dir = backup_dir_for(vm_name)
    if vm_dir is None or not vm_dir.exists() or keep < 1:
        return []
    files = sorted(vm_dir.glob("*.qcow2"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for stale in files[keep:]:
        try:
            stale.unlink()
            removed.append(stale.name)
        except OSError:
            pass
    return removed


def load_schedule() -> dict[str, Any]:
    try:
        data = json.loads(SCHEDULE_FILE.read_text())
        if isinstance(data, dict):
            return {
                "enabled": bool(data.get("enabled", False)),
                "vms": [name for name in data.get("vms", []) if isinstance(name, str) and valid_vm_name(name)],
                "keep": max(1, min(30, int(data.get("keep", DEFAULT_KEEP)))),
            }
    except Exception:
        pass
    return {"enabled": False, "vms": [], "keep": DEFAULT_KEEP}


def save_schedule(enabled: bool, vms: list[str], keep: int) -> dict[str, Any]:
    config = {
        "enabled": bool(enabled),
        "vms": sorted({name for name in vms if valid_vm_name(name)}),
        "keep": max(1, min(30, int(keep))),
    }
    with SCHEDULE_LOCK:
        SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = SCHEDULE_FILE.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(config, ensure_ascii=False, indent=2))
        tmp_path.replace(SCHEDULE_FILE)
    return config


def build_backup_script(vm_name: str, disk_path: str, target: Path) -> str:
    """Скрипт бэкапа: пауза работающей VM на время копии, сжатый qcow2, resume.

    Для выключенной VM virsh suspend/resume тихо провалятся — это нормально.
    """
    part = f"{target}.part"
    return (
        "set -uo pipefail; "
        f"mkdir -p {target.parent}; "
        f"virsh suspend {vm_name} >/dev/null 2>&1 && RESUME=1 || RESUME=0; "
        f"qemu-img convert -p -c -O qcow2 {disk_path} {part}; STATUS=$?; "
        f"[ \"$RESUME\" = 1 ] && virsh resume {vm_name} >/dev/null 2>&1; "
        f"[ $STATUS -ne 0 ] && rm -f {part} && exit $STATUS; "
        f"mv {part} {target}"
    )


def build_restore_script(disk_path: str, backup_file: Path) -> str:
    part = f"{disk_path}.restore"
    return (
        "set -euo pipefail; "
        f"qemu-img convert -p -O qcow2 {backup_file} {part}; "
        f"mv {part} {disk_path}"
    )


# ---- CLI-режим для ночного systemd-таймера ----

def _log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def first_disk_of(vm_name: str) -> str | None:
    result = _run(["virsh", "domblklist", vm_name, "--details"])
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 4 and parts[1] == "disk" and parts[3].endswith(".qcow2"):
            return parts[3]
    return None


def run_scheduled_backups() -> int:
    config = load_schedule()
    if not config["enabled"]:
        _log("Расписание выключено — выходим.")
        return 0
    if not config["vms"]:
        _log("Расписание включено, но список VM пуст.")
        return 0
    failures = 0
    for vm_name in config["vms"]:
        disk = first_disk_of(vm_name)
        if not disk:
            _log(f"{vm_name}: qcow2-диск не найден, пропуск.")
            failures += 1
            continue
        target = new_backup_path(vm_name)
        if target is None:
            _log(f"{vm_name}: некорректное имя VM, пропуск.")
            failures += 1
            continue
        _log(f"{vm_name}: бэкап {disk} → {target}")
        result = subprocess.run(["bash", "-c", build_backup_script(vm_name, disk, target)], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            removed = rotate_backups(vm_name, config["keep"])
            _log(f"{vm_name}: готово" + (f", ротация удалила: {', '.join(removed)}" if removed else ""))
        else:
            failures += 1
            _log(f"{vm_name}: ошибка бэкапа (код {result.returncode}): {result.stderr.strip()[:400]}")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--scheduled" in sys.argv:
        sys.exit(run_scheduled_backups())
    print("Использование: backup_core.py --scheduled", file=sys.stderr)
    sys.exit(2)
