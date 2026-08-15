#!/usr/bin/env python3
"""Лёгкий сборщик метрик хоста: кольцевой буфер в памяти, без БД и зависимостей.

Раз в METRICS_INTERVAL секунд снимаются CPU, память и load average хоста
плюс число работающих VM. Буфер хранит около часа истории для спарклайнов
на дашборде. Стоимость — одно чтение /proc и один вызов libvirt за цикл.
"""
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import virt_backend

METRICS_INTERVAL = 15
SAMPLES = deque(maxlen=240)  # ~1 час при интервале 15 секунд
_LOCK = threading.Lock()
_STARTED = threading.Event()
_prev_cpu: tuple[int, int] | None = None


def _read_cpu_times() -> tuple[int, int] | None:
    """(busy, total) джиффи из /proc/stat."""
    try:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        values = [int(v) for v in fields[:8]]
        idle = values[3] + values[4]  # idle + iowait
        total = sum(values)
        return total - idle, total
    except Exception:
        return None


def _cpu_percent() -> float:
    global _prev_cpu
    current = _read_cpu_times()
    if current is None:
        return 0.0
    if _prev_cpu is None:
        _prev_cpu = current
        return 0.0
    busy = current[0] - _prev_cpu[0]
    total = current[1] - _prev_cpu[1]
    _prev_cpu = current
    if total <= 0:
        return 0.0
    return round(100.0 * busy / total, 1)


def _memory_mb() -> tuple[int, int]:
    """(used_mb, total_mb) из /proc/meminfo."""
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            info[key] = int(rest.split()[0])
        total = info.get("MemTotal", 0) // 1024
        available = info.get("MemAvailable", 0) // 1024
        return max(0, total - available), total
    except Exception:
        return 0, 0


def _load_average() -> float:
    try:
        return float(Path("/proc/loadavg").read_text().split()[0])
    except Exception:
        return 0.0


def _running_vms() -> int:
    stats = virt_backend.domain_stats()
    if stats is not None:
        return len(stats)
    return -1  # backend недоступен — не считаем через subprocess, чтобы не грузить хост


def collect_sample() -> dict[str, Any]:
    used_mb, total_mb = _memory_mb()
    return {
        "ts": int(time.time()),
        "cpu": _cpu_percent(),
        "mem_used_mb": used_mb,
        "mem_total_mb": total_mb,
        "mem_percent": round(100.0 * used_mb / total_mb, 1) if total_mb else 0.0,
        "load1": _load_average(),
        "vms_running": _running_vms(),
    }


def _worker() -> None:
    _cpu_percent()  # первая выборка задаёт базу для дельты
    while True:
        sample = collect_sample()
        with _LOCK:
            SAMPLES.append(sample)
        time.sleep(METRICS_INTERVAL)


def start() -> None:
    if _STARTED.is_set():
        return
    _STARTED.set()
    threading.Thread(target=_worker, daemon=True, name="virtuality-metrics").start()


def snapshot(limit: int = 240) -> dict[str, Any]:
    with _LOCK:
        series = list(SAMPLES)[-limit:]
    return {
        "interval": METRICS_INTERVAL,
        "series": series,
        "current": series[-1] if series else None,
    }
