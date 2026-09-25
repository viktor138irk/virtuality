"""View helpers: turn virsh/system output into plain-language data for templates."""
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

VM_STATES = {
    "running": ("Работает", "success"),
    "idle": ("Работает", "success"),
    "blocked": ("Работает", "success"),
    "paused": ("Приостановлена", "warning"),
    "pmsuspended": ("Спящий режим", "warning"),
    "in shutdown": ("Выключается", "warning"),
    "shut off": ("Выключена", "neutral"),
    "shutoff": ("Выключена", "neutral"),
    "crashed": ("Сбой", "danger"),
    "dying": ("Останавливается", "warning"),
}

OPERATION_STATES = {
    "success": ("Готово", "success"),
    "error": ("Ошибка", "danger"),
    "running": ("Выполняется", "info"),
    "queued": ("В очереди", "neutral"),
}

SERVICE_LABELS = {
    "libvirtd": "Виртуализация",
    "virtlogd": "Журналы машин",
    "web": "Панель управления",
}

PORT_PRESETS = [
    {"port": "22", "label": "SSH — удалённый доступ к Linux", "protocol": "tcp"},
    {"port": "80", "label": "Сайт (HTTP)", "protocol": "tcp"},
    {"port": "443", "label": "Сайт (HTTPS)", "protocol": "tcp"},
    {"port": "3389", "label": "Удалённый рабочий стол Windows (RDP)", "protocol": "tcp"},
    {"port": "25565", "label": "Minecraft", "protocol": "tcp"},
    {"port": "3306", "label": "MySQL / MariaDB", "protocol": "tcp"},
    {"port": "5432", "label": "PostgreSQL", "protocol": "tcp"},
    {"port": "1194", "label": "OpenVPN", "protocol": "udp"},
    {"port": "51820", "label": "WireGuard VPN", "protocol": "udp"},
    {"port": "5060", "label": "SIP-телефония", "protocol": "udp"},
    {"port": "10000-20000", "label": "Голос VoIP (RTP)", "protocol": "udp"},
]

WELL_KNOWN_PORTS = {int(item["port"]): item["label"].split(" (")[0].split(" — ")[0] for item in PORT_PRESETS if item["port"].isdigit()}

VM_PRESETS = [
    {"id": "mini", "title": "Мини", "hint": "Роутер, VPN, лёгкий сервис", "vcpus": 1, "memory": 1024, "disk_size": 10},
    {"id": "standard", "title": "Стандарт", "hint": "Сайт, база данных, Linux-сервер", "vcpus": 2, "memory": 4096, "disk_size": 40},
    {"id": "power", "title": "Мощная", "hint": "Windows, 1С, игровой сервер", "vcpus": 4, "memory": 8192, "disk_size": 80},
]


def vm_state(state: str) -> dict[str, Any]:
    key = (state or "").strip().lower()
    label, tone = VM_STATES.get(key, (state or "Неизвестно", "neutral"))
    return {"label": label, "tone": tone, "running": tone == "success", "raw": state}


def operation_state(status: str) -> dict[str, str]:
    label, tone = OPERATION_STATES.get((status or "").lower(), (status or "—", "neutral"))
    return {"label": label, "tone": tone}


def service_state(state: str) -> dict[str, str]:
    if state == "active":
        return {"label": "Работает", "tone": "success"}
    if state in ("activating", "reloading"):
        return {"label": "Запускается", "tone": "warning"}
    if state == "failed":
        return {"label": "Сбой", "tone": "danger"}
    return {"label": "Остановлена", "tone": "neutral"}


def _kib_to_mb(value: str) -> int:
    match = re.search(r"(\d+)", value or "")
    if not match:
        return 0
    amount = int(match.group(1))
    low = (value or "").lower()
    if "mib" in low:
        return amount
    if "gib" in low:
        return amount * 1024
    return amount // 1024


def parse_dominfo(text: str) -> dict[str, Any]:
    fields: dict[str, str] = {}
    for line in (text or "").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
    try:
        vcpus = int(fields.get("cpu(s)", "0") or 0)
    except ValueError:
        vcpus = 0
    autostart = fields.get("autostart", "").lower()
    return {
        "state": fields.get("state", ""),
        "vcpus": vcpus,
        "memory_mb": _kib_to_mb(fields.get("used memory", "")) or _kib_to_mb(fields.get("max memory", "")),
        "max_memory_mb": _kib_to_mb(fields.get("max memory", "")),
        "autostart": autostart in ("enable", "enabled", "yes", "on"),
        "autostart_known": bool(autostart),
        "persistent": fields.get("persistent", "") == "yes",
        "os_type": fields.get("os type", ""),
        "uuid": fields.get("uuid", ""),
    }


def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or set(stripped) <= {"-", " "}:
            continue
        rows.append(stripped.split())
    return rows[1:] if rows else []


def parse_domblklist(text: str) -> list[dict[str, str]]:
    disks = []
    for parts in _table_rows(text):
        if len(parts) >= 4:
            kind, device, target, source = parts[0], parts[1], parts[2], " ".join(parts[3:])
        elif len(parts) >= 2:
            kind, device, target, source = "file", "disk", parts[0], " ".join(parts[1:])
        else:
            continue
        if source == "-":
            source = ""
        disks.append({
            "type": kind,
            "device": device,
            "target": target,
            "source": source,
            "name": Path(source).name if source else "Пусто",
            "is_cdrom": device == "cdrom",
            "label": "Привод CD/DVD" if device == "cdrom" else "Диск",
        })
    return disks


def parse_domiflist(text: str) -> list[dict[str, str]]:
    interfaces = []
    for parts in _table_rows(text):
        if len(parts) < 2:
            continue
        padded = parts + ["—"] * (5 - len(parts))
        interfaces.append({"interface": padded[0], "type": padded[1], "source": padded[2], "model": padded[3], "mac": padded[4]})
    return interfaces


def format_mb(value: int | float) -> str:
    value = float(value or 0)
    if value >= 1024:
        gb = value / 1024
        return f"{gb:.0f} ГБ" if gb >= 10 or gb.is_integer() else f"{gb:.1f} ГБ"
    return f"{value:.0f} МБ"


def format_gb(value: float) -> str:
    if value >= 1024:
        return f"{value / 1024:.1f} ТБ"
    return f"{value:.0f} ГБ" if value >= 10 else f"{value:.1f} ГБ"


def _meminfo() -> dict[str, int]:
    data = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            data[key] = int(value.split()[0])
    except (OSError, ValueError):
        pass
    return data


def host_stats(storage_path: Path) -> dict[str, Any]:
    cpu_count = os.cpu_count() or 1
    try:
        load1 = float(Path("/proc/loadavg").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        load1 = 0.0
    mem = _meminfo()
    mem_total = mem.get("MemTotal", 0) / 1024 / 1024
    mem_used = max(0.0, mem_total - mem.get("MemAvailable", 0) / 1024 / 1024)
    path = storage_path if storage_path.exists() else Path("/")
    try:
        usage = shutil.disk_usage(path)
        disk_total, disk_used, disk_free = usage.total / 1024 ** 3, usage.used / 1024 ** 3, usage.free / 1024 ** 3
    except OSError:
        disk_total = disk_used = disk_free = 0.0

    def pct(used: float, total: float) -> int:
        return int(round(min(100.0, max(0.0, used / total * 100)))) if total else 0

    return {
        "cpu_count": cpu_count,
        "cpu_pct": pct(load1, cpu_count),
        "load1": load1,
        "mem_total": format_gb(mem_total),
        "mem_used": format_gb(mem_used),
        "mem_pct": pct(mem_used, mem_total),
        "disk_total": format_gb(disk_total),
        "disk_used": format_gb(disk_used),
        "disk_free": format_gb(disk_free),
        "disk_pct": pct(disk_used, disk_total),
    }


def level_tone(pct: int) -> str:
    return "danger" if pct >= 90 else "warning" if pct >= 75 else "success"


def greeting(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    if 5 <= hour < 12:
        return "Доброе утро"
    if 12 <= hour < 18:
        return "Добрый день"
    if 18 <= hour < 23:
        return "Добрый вечер"
    return "Доброй ночи"


def plural(count: int, one: str, few: str, many: str) -> str:
    count = abs(int(count))
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def port_service(port: int) -> str:
    return WELL_KNOWN_PORTS.get(int(port or 0), "")


def format_bytes(value: int | float) -> str:
    value = float(value or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            return f"{value:.0f} {unit}" if value >= 10 or unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.0f} ТБ"


def parse_virsh_list_titles(text: str) -> dict[str, str]:
    """`virsh list --all --title` → {имя: название}. Колонки virsh выровнены
    пробелами, поэтому название берём по смещению колонки Title из заголовка:
    так не ломаются состояния с пробелом («shut off») и названия из нескольких слов."""
    lines = (text or "").splitlines()
    if not lines:
        return {}
    header = lines[0]
    name_col, title_col = re.search(r"\bName\b", header), re.search(r"\bTitle\b", header)
    if not name_col or not title_col:
        return {}
    titles: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped or set(stripped) <= {"-"}:
            continue
        name_part = line[name_col.start():].split()
        if not name_part:
            continue
        title = line[title_col.start():].strip()
        if title:
            titles[name_part[0]] = title
    return titles
