"""Facts about the node (network, disks, time zone, first-boot progress) and
actions that change the node itself. Actions go through virtuality-ctl so the
panel and the command line behave identically."""
import ipaddress
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from core import CONFIG_DIR, STORAGE_DIR, cmd_error, run_cmd

CTL = "/usr/local/bin/virtuality-ctl"
NODE_ENV = Path(os.environ.get("VIRTUALITY_NODE_ENV", str(CONFIG_DIR / "web.env")))
SETUP_STATE = CONFIG_DIR / "setup.json"
SETUP_DONE = CONFIG_DIR / "setup_done"
WIZARD_FILE = CONFIG_DIR / "wizard.json"
FIRSTBOOT_LOG = Path("/var/log/virtuality/firstboot.log")
MIN_SPARE_DISK = 16 * 1024 ** 3

POPULAR_TIMEZONES = [
    "Europe/Moscow", "Europe/Kaliningrad", "Europe/Samara", "Asia/Yekaterinburg", "Asia/Omsk", "Asia/Novosibirsk",
    "Asia/Krasnoyarsk", "Asia/Irkutsk", "Asia/Yakutsk", "Asia/Vladivostok", "Asia/Magadan", "Asia/Kamchatka",
    "Europe/Minsk", "Europe/Kiev", "Asia/Almaty", "Asia/Tashkent", "Asia/Tbilisi", "Asia/Yerevan", "Asia/Baku",
    "Europe/Berlin", "Europe/London", "Europe/Istanbul", "Asia/Dubai", "America/New_York", "UTC",
]


# ---------------------------------------------------------------- node settings
def node_settings() -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        for raw in NODE_ENV.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    data.setdefault("VIRTUALITY_WEB_PORT", "8088")
    data.setdefault("VIRTUALITY_TLS_PORT", "8443")
    data.setdefault("VIRTUALITY_TLS", "0")
    data.setdefault("VIRTUALITY_AUTO_UPDATE", "1")
    data.setdefault("VIRTUALITY_UPDATE_CHANNEL", "stable")
    return data


def panel_url(host: str, settings: dict[str, str] | None = None) -> str:
    settings = settings or node_settings()
    if settings.get("VIRTUALITY_TLS") == "1":
        return f"https://{host}:{settings['VIRTUALITY_TLS_PORT']}"
    return f"http://{host}:{settings['VIRTUALITY_WEB_PORT']}"


def ctl_available() -> bool:
    return Path(CTL).exists()


def ctl(*args: str, timeout: int = 60, stdin: str | None = None) -> dict[str, Any]:
    if not ctl_available():
        return {"ok": False, "code": -1, "stdout": "", "stderr": "virtuality-ctl не установлен", "cmd": CTL}
    try:
        result = subprocess.run([CTL, *args], capture_output=True, text=True, timeout=timeout, check=False, input=stdin)
        return {"ok": result.returncode == 0, "code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "cmd": " ".join([CTL, *args])}
    except Exception as exc:
        return {"ok": False, "code": -1, "stdout": "", "stderr": str(exc), "cmd": " ".join([CTL, *args])}


def ctl_detached(*args: str, delay_seconds: int = 2) -> dict[str, Any]:
    """Run a virtuality-ctl command after the HTTP response is sent (the command may restart the panel)."""
    unit = f"virtuality-apply-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    return run_cmd(["systemd-run", "--unit", unit, "--collect", "--quiet", f"--on-active={delay_seconds}", CTL, *args], timeout=15)


# ---------------------------------------------------------------- first boot state
def setup_state() -> dict[str, Any]:
    try:
        data = json.loads(SETUP_STATE.read_text())
    except Exception:
        data = {}
    data.setdefault("stage", "done" if not SETUP_STATE.exists() else "installing")
    data.setdefault("steps", [])
    data.setdefault("message", "")
    data["installed"] = data["stage"] == "done"
    data["log_tail"] = ""
    if FIRSTBOOT_LOG.exists():
        try:
            data["log_tail"] = "\n".join(FIRSTBOOT_LOG.read_text(errors="replace").splitlines()[-40:])
        except OSError:
            pass
    return data


def wizard_done() -> bool:
    return SETUP_DONE.exists()


def mark_wizard_done() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETUP_DONE.write_text(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")


def wizard_choices() -> dict[str, Any]:
    try:
        return json.loads(WIZARD_FILE.read_text())
    except Exception:
        return {}


def save_wizard_choices(**changes: Any) -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = wizard_choices()
    data.update(changes)
    WIZARD_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return data


# ---------------------------------------------------------------- hardware & time
def hardware_summary() -> dict[str, Any]:
    cpu_model = "—"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() == "model name" and value.strip():
                cpu_model = value.strip()
                break
    except OSError:
        pass
    mem_total = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    try:
        usage = shutil.disk_usage(str(STORAGE_DIR) if STORAGE_DIR.exists() else "/")
        disk_total, disk_free = usage.total, usage.free
    except OSError:
        disk_total = disk_free = 0
    return {
        "hostname": run_cmd(["hostname"])["stdout"] or "—",
        "cpu_model": cpu_model,
        "cpu_count": os.cpu_count() or 1,
        "mem_total": mem_total,
        "disk_total": disk_total,
        "disk_free": disk_free,
        "kvm": Path("/dev/kvm").exists(),
    }


def timezones() -> dict[str, Any]:
    current = run_cmd(["timedatectl", "show", "-p", "Timezone", "--value"])["stdout"] or "UTC"
    listed = run_cmd(["timedatectl", "list-timezones"], timeout=10)
    names = [line.strip() for line in listed["stdout"].splitlines() if "/" in line or line.strip() == "UTC"] if listed["ok"] else []
    if not names:
        names = list(POPULAR_TIMEZONES)
    popular = [tz for tz in POPULAR_TIMEZONES if tz in names]
    return {"current": current, "popular": popular, "all": names}


def set_timezone(tz: str) -> tuple[bool, str]:
    if not re.fullmatch(r"[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,2}", tz or ""):
        return False, "Некорректный часовой пояс"
    result = ctl("timezone", tz, timeout=30)
    return result["ok"], cmd_error(result, "Не удалось сменить часовой пояс") if not result["ok"] else tz


# ---------------------------------------------------------------- network facts
def _ip_json(*args: str) -> list[dict[str, Any]]:
    result = run_cmd(["ip", "-j", *args], timeout=8)
    if not result["ok"]:
        return []
    try:
        data = json.loads(result["stdout"] or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def network_facts() -> dict[str, Any]:
    routes = _ip_json("route", "show", "default")
    default = routes[0] if routes else {}
    iface = default.get("dev", "")
    gateway = default.get("gateway", "")
    address = ""
    prefix = 0
    mac = ""
    for entry in _ip_json("addr", "show", iface) if iface else []:
        mac = entry.get("address", "") or mac
        for info in entry.get("addr_info", []):
            if info.get("family") == "inet" and not address:
                address = info.get("local", "")
                prefix = int(info.get("prefixlen", 0) or 0)
    wireless = bool(iface) and Path(f"/sys/class/net/{iface}/wireless").exists()
    bridge_present = Path("/sys/class/net/br0").exists()
    on_bridge = iface == "br0"
    private = False
    try:
        private = ipaddress.ip_address(address).is_private if address else False
    except ValueError:
        private = False
    is_vps = bool(address) and not private
    virt = run_cmd(["systemd-detect-virt"], timeout=5)["stdout"]
    if virt and virt != "none":
        is_vps = True
    if on_bridge:
        recommended = "bridge"
    elif wireless or is_vps or not iface:
        recommended = "nat"
    else:
        recommended = "bridge"
    revert_armed = run_cmd(["systemctl", "is-active", "virtuality-netplan-revert.timer"], timeout=5)["stdout"] == "active"
    return {
        "interface": iface, "gateway": gateway, "address": address, "prefix": prefix, "mac": mac,
        "wireless": wireless, "private": private, "is_vps": is_vps, "virt": virt or "none",
        "bridge_present": bridge_present, "on_bridge": on_bridge, "recommended": recommended,
        "revert_armed": revert_armed,
        "bridge_possible": bool(iface) and not wireless and not on_bridge,
    }


def enable_bridge(iface: str, mode: str = "dhcp") -> tuple[bool, str]:
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,15}", iface or "") or mode not in ("dhcp", "static"):
        return False, "Некорректный интерфейс"
    result = ctl_detached("bridge", iface, mode, delay_seconds=1)
    return result["ok"], cmd_error(result, "Не удалось запустить настройку сети") if not result["ok"] else "Сеть перенастраивается"


def confirm_bridge() -> tuple[bool, str]:
    result = ctl("bridge-confirm", timeout=30)
    return result["ok"], cmd_error(result, "Не удалось подтвердить сеть") if not result["ok"] else "Сеть подтверждена"


def revert_bridge() -> tuple[bool, str]:
    result = ctl_detached("bridge-revert", delay_seconds=1)
    return result["ok"], cmd_error(result, "Не удалось вернуть прежнюю сеть") if not result["ok"] else "Возвращаем прежнюю сеть"


# ---------------------------------------------------------------- storage facts
def _lsblk() -> list[dict[str, Any]]:
    result = run_cmd(["lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,SIZE,FSTYPE,MOUNTPOINT,MODEL,TRAN,RM,PKNAME"], timeout=10)
    if not result["ok"]:
        return []
    try:
        return json.loads(result["stdout"] or "{}").get("blockdevices", [])
    except json.JSONDecodeError:
        return []


def spare_disks() -> list[dict[str, Any]]:
    """Whole disks with no partitions or file system: safe candidates for VM storage."""
    disks = []
    for dev in _lsblk():
        if dev.get("type") != "disk" or dev.get("children") or dev.get("fstype") or dev.get("mountpoint"):
            continue
        if str(dev.get("rm")) in ("1", "True", "true") or dev.get("tran") == "usb":
            continue
        size = int(dev.get("size") or 0)
        if size < MIN_SPARE_DISK:
            continue
        disks.append({"path": dev.get("path") or f"/dev/{dev.get('name')}", "name": dev.get("name"), "size": size, "model": (dev.get("model") or "").strip() or "Диск", "tran": dev.get("tran") or ""})
    return disks


def storage_facts() -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(str(STORAGE_DIR) if STORAGE_DIR.exists() else "/")
        total, free = usage.total, usage.free
    except OSError:
        total = free = 0
    mount = run_cmd(["findmnt", "-n", "-o", "SOURCE,TARGET", "--target", str(STORAGE_DIR)], timeout=5)["stdout"]
    source, _, target = mount.partition(" ")
    return {"path": str(STORAGE_DIR), "total": total, "free": free, "source": source, "mount_target": target.strip() or "/", "dedicated": target.strip() == str(STORAGE_DIR), "spare": spare_disks()}


def use_disk_for_storage(path: str) -> tuple[bool, str]:
    if not any(d["path"] == path for d in spare_disks()):
        return False, "Этот диск нельзя использовать: он занят или на нём уже есть данные"
    result = ctl("storage-use", path, timeout=600)
    return result["ok"], cmd_error(result, "Не удалось подготовить диск") if not result["ok"] else "Диск подключён для хранения машин"


# ---------------------------------------------------------------- account & settings
def change_password(user: str, new_password: str) -> tuple[bool, str]:
    if len(new_password) < 6:
        return False, "Пароль должен быть не короче 6 символов"
    result = ctl("passwd", user, stdin=new_password + "\n", timeout=30)
    return result["ok"], cmd_error(result, "Не удалось сменить пароль") if not result["ok"] else "Пароль изменён"


def apply_settings(changes: dict[str, str]) -> tuple[bool, str]:
    allowed = {"VIRTUALITY_WEB_PORT", "VIRTUALITY_TLS", "VIRTUALITY_TLS_PORT", "VIRTUALITY_AUTO_UPDATE", "VIRTUALITY_UPDATE_CHANNEL"}
    pairs = [f"{key}={value}" for key, value in changes.items() if key in allowed and re.fullmatch(r"[A-Za-z0-9._-]+", str(value))]
    if not pairs:
        return False, "Нечего применять"
    result = ctl_detached("set", *pairs)
    return result["ok"], cmd_error(result, "Не удалось применить настройки") if not result["ok"] else "Настройки применяются"


def power(action: str) -> tuple[bool, str]:
    if action not in ("reboot", "poweroff"):
        return False, "Неизвестное действие"
    result = ctl_detached("power", action, delay_seconds=3)
    return result["ok"], cmd_error(result, "Не удалось выполнить") if not result["ok"] else ("Сервер перезагружается" if action == "reboot" else "Сервер выключается")


def backup_config() -> tuple[bool, str]:
    result = ctl("backup-config", timeout=120)
    return result["ok"], cmd_error(result, "Не удалось создать архив настроек") if not result["ok"] else result["stdout"]
