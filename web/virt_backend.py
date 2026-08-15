#!/usr/bin/env python3
"""Быстрый доступ к libvirt через python-байндинги.

Одно постоянное соединение вместо десятков subprocess-вызовов virsh на
каждую загрузку страницы — критично для Raspberry Pi и слабых VPS.
Если libvirt-python не установлен или libvirtd недоступен, модуль честно
сообщает об этом через available(), и приложение работает через virsh.
"""
import threading
from typing import Any

try:
    import libvirt  # python3-libvirt / libvirt-python
    # Отключаем печать ошибок libvirt в stderr — ошибки обрабатываются кодом.
    libvirt.registerErrorHandler(lambda ctx, err: None, None)
except ImportError:
    libvirt = None

_CONN_LOCK = threading.Lock()
_conn = None

# Коды состояний libvirt → строки в стиле virsh, которые уже ждёт UI.
_STATE_NAMES = {
    0: "no state",
    1: "running",
    2: "blocked",
    3: "paused",
    4: "in shutdown",
    5: "shut off",
    6: "crashed",
    7: "pmsuspended",
}


def _connection():
    global _conn
    if libvirt is None:
        return None
    with _CONN_LOCK:
        if _conn is not None:
            try:
                if _conn.isAlive():
                    return _conn
            except Exception:
                pass
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
        try:
            _conn = libvirt.open("qemu:///system")
        except Exception:
            _conn = None
        return _conn


def available() -> bool:
    return _connection() is not None


def list_domains() -> list[dict[str, Any]] | None:
    """Список всех доменов с состоянием и автозапуском. None — backend недоступен."""
    conn = _connection()
    if conn is None:
        return None
    rows = []
    try:
        for dom in conn.listAllDomains(0):
            try:
                state_code = dom.state()[0]
                dom_id = dom.ID()
                rows.append({
                    "id": str(dom_id) if dom_id > 0 else "-",
                    "name": dom.name(),
                    "state": _STATE_NAMES.get(state_code, "unknown"),
                    "autostart_enabled": bool(dom.autostart()),
                })
            except Exception:
                continue
    except Exception:
        return None
    rows.sort(key=lambda row: row["name"])
    return rows


def domain_stats() -> dict[str, dict[str, int]] | None:
    """cpu_time (нс) и память (KiB) работающих доменов для сбора метрик."""
    conn = _connection()
    if conn is None:
        return None
    stats: dict[str, dict[str, int]] = {}
    try:
        for dom in conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE):
            try:
                info = dom.info()  # [state, maxMem, memory, nrVirtCpu, cpuTime]
                stats[dom.name()] = {"cpu_time_ns": int(info[4]), "memory_kb": int(info[2]), "max_memory_kb": int(info[1]), "vcpus": int(info[3])}
            except Exception:
                continue
    except Exception:
        return None
    return stats


def node_memory_mb() -> dict[str, int] | None:
    """Память хоста по данным libvirt (fallback для метрик)."""
    conn = _connection()
    if conn is None:
        return None
    try:
        info = conn.getInfo()  # [model, memory_mb, cpus, ...]
        return {"total_mb": int(info[1]), "cpus": int(info[2])}
    except Exception:
        return None
