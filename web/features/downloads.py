"""Скачивание образов ОС по ссылке и из встроенного каталога готовых систем.

Файл качается на сервере в фоне как операция типа «download»: прогресс,
скорость и оставшееся время видны на страницах «Образы ОС» и в «Задачах».
Пока идёт загрузка, файл лежит рядом как `.<имя>.part` и переименовывается
только после успешного завершения (и проверки контрольной суммы для
образов из каталога). Скачивание можно отменить — недокачанный файл удаляется.
"""
import hashlib
import json
import lzma
import re
import shutil
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

import catalog
import core
import host_profile
from core import (
    APP_VERSION,
    append_operation_log,
    finish_operation,
    new_operation,
    read_operation,
    redirect_with_message,
    require_api_auth,
    require_auth,
    run_cmd,
    run_operation,
    update_operation,
)

router = APIRouter()

CHUNK_SIZE = 1024 * 1024
TIMEOUT = 30
RESERVE_BYTES = 512 * 1024 * 1024
SUMS_MAX_BYTES = 4 * 1024 * 1024
USER_AGENT = f"Virtuality/{APP_VERSION}"
ISO_POOL = "virtuality-iso"

KINDS: dict[str, dict[str, Any]] = {
    "iso": {"suffixes": (".iso",), "page": "/iso", "label": "установочный образ", "hint": "Ссылка должна вести на файл .iso"},
    "disk": {"suffixes": (".qcow2", ".img", ".raw", ".img.xz"), "page": "/disk-images", "label": "образ диска", "hint": "Подойдут файлы .qcow2, .img, .raw и сжатые .img.xz"},
}

# Состояние скачиваний этого процесса (под одним замком):
#  - CANCEL_REQUESTS — операции, которые попросили остановить; поток загрузки
#    проверяет множество между кусками файла, при проверке суммы и распаковке;
#  - LIVE_DOWNLOADS — операция → путь к файлу для скачиваний, которыми владеет
#    живой поток. Операция без потока (после перезапуска панели) — сирота: её
#    закрывают сразу, ничего не ожидая.
# Множества живут в памяти одного процесса: панель запускается с одним воркером
# uvicorn. На случай нескольких процессов поток раз в секунду читает флаг
# cancel_requested и из JSON операции.
STATE_LOCK = threading.Lock()
CANCEL_REQUESTS: set[str] = set()
LIVE_DOWNLOADS: dict[str, str] = {}
# Проверки «файл уже есть» / «уже скачивается» и создание операции идут под одним
# замком, чтобы два запуска одного файла не прошли проверку одновременно.
START_LOCK = threading.Lock()

# Понятные объяснения частых ошибок HTTP; остальные коды показываем числом.
HTTP_ERRORS = {
    401: "Сервер требует вход по паролю (ошибка 401)",
    403: "Сервер запретил скачивание (ошибка 403)",
    404: "Файл по ссылке не найден (ошибка 404)",
    410: "Файл по ссылке больше не выдаётся (ошибка 410)",
    429: "Сервер просит подождать: слишком много запросов (ошибка 429)",
    500: "На сервере произошла ошибка (500)",
    502: "Сервер временно недоступен (ошибка 502)",
    503: "Сервер временно недоступен (ошибка 503)",
    504: "Сервер временно недоступен (ошибка 504)",
}


class DownloadError(ValueError):
    """Понятная пользователю ошибка проверки ссылки или файла."""


class DownloadCancelled(Exception):
    """Пользователь отменил скачивание во время проверки суммы или распаковки."""


# ---------------------------------------------------------------- formatting
def human_size(value: float | int | None) -> str:
    """1234567 → «1,2 МБ». Десятичная запятая, как принято в русском."""
    value = float(value or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            if unit == "Б" or value >= 100:
                return f"{value:.0f} {unit}"
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text.replace('.', ',')} {unit}"
        value /= 1024
    return f"{value:.0f} ТБ"


def human_eta(seconds: float | None) -> str:
    """Оставшееся время словами: «40 с», «4 мин», «1 ч 12 мин»."""
    if seconds is None or seconds < 0:
        return ""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{max(seconds, 1)} с"
    minutes = (seconds + 30) // 60
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"


def progress_message(done: int, total: int | None, speed: float | None) -> str:
    """«245 МБ из 3,1 ГБ · 12 МБ/с · осталось 4 мин»."""
    parts = [f"{human_size(done)} из {human_size(total)}" if total else human_size(done)]
    if speed and speed > 0:
        parts.append(f"{human_size(speed)}/с")
        if total and total > done:
            parts.append(f"осталось {human_eta((total - done) / speed)}")
    return " · ".join(parts)


# ---------------------------------------------------------------- validation
def validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise DownloadError("Вставьте ссылку на файл")
    if len(url) > 2048 or any(ch.isspace() for ch in url):
        raise DownloadError("Ссылка выглядит некорректно")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise DownloadError("Ссылка выглядит некорректно") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise DownloadError("Ссылка должна начинаться с http:// или https://")
    if not parts.hostname:
        raise DownloadError("В ссылке не указан адрес сервера")
    encode_url(url)  # кириллица в домене или пути, порт — проверяем сразу, а не в фоне
    return url


def encode_url(url: str) -> str:
    """Ссылка в виде, который понимает urllib: домен с кириллицей — в IDNA, не-ASCII
    символы в пути и параметрах — в проценты. Уже закодированные ссылки не меняются."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError as exc:
        raise DownloadError("Ссылка выглядит некорректно") from exc
    try:
        host = (parts.hostname or "").encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DownloadError("Ссылка содержит символы, которые сервер не понимает") from exc
    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc = f"{netloc}:{port}"
    if "@" in parts.netloc:
        netloc = f"{parts.netloc.rsplit('@', 1)[0]}@{netloc}"
    path = quote(parts.path, safe="/%:@!$&'()*+,;=~")
    query = quote(parts.query, safe="/%:@!$&'()*+,;=?~")
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def filename_from_url(url: str, kind: str) -> str:
    """Имя файла из ссылки, приведённое к безопасному виду (как при загрузке с компьютера)."""
    if kind not in KINDS:
        raise DownloadError("Неизвестный тип образа")
    original = unquote(urlsplit(url).path).rstrip("/").rsplit("/", 1)[-1].strip()
    if not original:
        raise DownloadError("В ссылке нет имени файла — она должна вести на сам файл, а не на страницу")
    lower = original.lower()
    suffix = next((item for item in KINDS[kind]["suffixes"] if lower.endswith(item)), None)
    if not suffix:
        allowed = ", ".join(KINDS[kind]["suffixes"])
        raise DownloadError(f"Файл «{original}» не подходит: нужен {KINDS[kind]['label']} ({allowed})")
    stem = original[: -len(suffix)]
    stem = re.sub(r"\s+", "-", stem.strip())
    stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", stem)
    stem = stem.strip("._-")[:120]
    if not stem:
        stem = "virtuality-iso" if kind == "iso" else "virtuality-disk"
    name = f"{stem}{suffix}"
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,180}", name):
        raise DownloadError("Не удалось составить безопасное имя файла из ссылки")
    return name


def kind_dir(kind: str) -> Path:
    return core.ISO_DIR if kind == "iso" else core.DISK_IMAGES_DIR


def target_path(kind: str, filename: str) -> Path:
    directory = kind_dir(kind)
    directory.mkdir(parents=True, exist_ok=True)
    path = (directory / filename).resolve()
    if directory.resolve() not in path.parents:
        raise DownloadError("Недопустимое имя файла")
    return path


def part_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.part")


def free_space_ok(directory: Path, needed: int | None) -> tuple[bool, int]:
    free = shutil.disk_usage(str(directory)).free
    return free >= (needed or 0) + RESERVE_BYTES, free


# ---------------------------------------------------------------- checksums
HASH_RE = re.compile(r"^[0-9a-fA-F]{32,128}$")


def parse_checksums(text: str, filename: str) -> str | None:
    """Хеш файла из файла контрольных сумм.

    Понимает «<hash> *name» и «<hash>  name» (Ubuntu, Debian), BSD-стиль
    «SHA256 (name) = <hash>» и файл из одного хеша (Alpine *.sha512).
    """
    bare: str | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        bsd = re.match(r"^[A-Za-z0-9-]+\s*\((.+)\)\s*=\s*([0-9a-fA-F]{32,128})$", line)
        if bsd:
            if Path(bsd.group(1).strip()).name == filename:
                return bsd.group(2).lower()
            continue
        pieces = line.split(None, 1)
        if len(pieces) == 2 and HASH_RE.match(pieces[0]):
            name = pieces[1].strip().lstrip("*").strip()
            if Path(name).name == filename:
                return pieces[0].lower()
            continue
        if len(pieces) == 1 and HASH_RE.match(pieces[0]) and bare is None:
            bare = pieces[0].lower()
    return bare


def file_digest(path: Path, algo: str, on_progress: Any = None) -> str:
    digest = hashlib.new(algo)
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            if on_progress:
                on_progress(len(chunk))
    return digest.hexdigest()


# ---------------------------------------------------------------- operations
def unfinished_downloads() -> list[dict[str, Any]]:
    """Незавершённые скачивания по всему каталогу операций, а не по последним 50,
    как running_operations: зависшая старая операция иначе не попадёт в проверку."""
    core.ensure_operations_dir()
    found = []
    for path in core.OPERATIONS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("type") == "download" and data.get("status") in ("queued", "running"):
            found.append(data)
    found.sort(key=lambda op: str(op.get("created_at") or ""))
    return found


def is_live(operation_id: str) -> bool:
    with STATE_LOCK:
        return operation_id in LIVE_DOWNLOADS


def release_download(operation_id: str) -> None:
    with STATE_LOCK:
        LIVE_DOWNLOADS.pop(operation_id, None)
        CANCEL_REQUESTS.discard(operation_id)


def reap_stale_downloads() -> int:
    """Скачивания без живого потока (панель перезапускалась) закрываются, а их
    недокачанные .part удаляются — иначе они лежали бы в каталоге образов вечно.

    Операции всех типов закрывает core.interrupt_orphaned_operations() при старте;
    здесь остаётся убрать файлы .part, которых та не касается. Возвращает число
    закрытых операций."""
    count = 0
    for op in unfinished_downloads():
        if is_live(op["id"]):
            continue
        message = "Скачивание прервано: панель была перезапущена. Запустите его заново"
        if op.get("target_path"):
            part_path(Path(op["target_path"])).unlink(missing_ok=True)
        release_download(op["id"])
        finish_operation(op, False, message, interrupted=True)
        count += 1
    with STATE_LOCK:
        live_targets = set(LIVE_DOWNLOADS.values())
    for directory in (core.ISO_DIR, core.DISK_IMAGES_DIR):
        if not directory.is_dir():
            continue
        for part in directory.glob(".*.part"):
            if str(part.with_name(part.name[1:-5]).resolve()) in live_targets:
                continue
            part.unlink(missing_ok=True)
    return count


# ---------------------------------------------------------------- catalog
def host_arch() -> str:
    arch = str(host_profile.load_host_profile().get("arch") or "").lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(arch, arch or "x86_64")


def image_candidates(filename: str) -> list[str]:
    """Имена файлов, под которыми образ может лежать после скачивания:
    сам файл, распакованный .img для .img.xz и qcow2 после конвертации."""
    names = [filename]
    lower = filename.lower()
    if lower.endswith(".img.xz"):
        names.append(filename[:-3])
        names.append(filename[:-7] + ".qcow2")
    elif lower.endswith((".img", ".raw")):
        names.append(filename[: filename.rfind(".")] + ".qcow2")
    return names


def catalog_entries(arch: str | None = None, active: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Каталог готовых систем для процессора сервера с пометками «Загружен» / «Скачивается».
    Список идущих скачиваний можно передать готовым, чтобы не читать каталог операций дважды."""
    arch = arch or host_arch()
    ops = unfinished_downloads() if active is None else active
    by_catalog_id = {op.get("catalog_id"): op for op in ops if op.get("catalog_id")}
    entries: list[dict[str, Any]] = []
    for item in catalog.CLOUD_IMAGES:
        source = item.get("arches", {}).get(arch)
        if not source:
            continue
        try:
            filename = filename_from_url(source["url"], "disk")
        except DownloadError:
            continue
        ready = next((name for name in reversed(image_candidates(filename)) if (core.DISK_IMAGES_DIR / name).exists()), None)
        operation = by_catalog_id.get(item["id"])
        entries.append({
            "id": item["id"], "title": item["title"], "vendor": item.get("vendor", ""), "hint": item.get("hint", ""),
            "recommended": bool(item.get("recommended")), "login": item.get("login", ""), "filename": filename,
            "uefi": bool(source.get("uefi")), "status": "downloading" if operation else "ready" if ready else "none",
            "ready_file": ready, "operation_id": operation["id"] if operation else None,
        })
    return entries


def active_downloads(kind: str | None = None, ops: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    result = []
    for op in unfinished_downloads() if ops is None else ops:
        if kind and op.get("download_kind") != kind:
            continue
        op["indeterminate"] = not op.get("total_bytes") and op.get("status") == "running"
        result.append(op)
    return result


def downloads_panel(kind: str) -> dict[str, Any]:
    """Данные для карточек скачивания на страницах /iso и /disk-images (глобальная функция шаблонов).
    Каталог операций читается один раз на вызов; страница вызывает функцию один раз и передаёт результат во вставки."""
    kind = kind if kind in KINDS else "iso"
    ops = unfinished_downloads()
    return {"kind": kind, "page": KINDS[kind]["page"], "hint": KINDS[kind]["hint"], "active": active_downloads(kind, ops), "catalog": catalog_entries(active=ops) if kind == "disk" else []}


# ---------------------------------------------------------------- cancel
def cancel_requested(operation_id: str) -> bool:
    with STATE_LOCK:
        return operation_id in CANCEL_REQUESTS


def clear_cancel(operation_id: str) -> None:
    with STATE_LOCK:
        CANCEL_REQUESTS.discard(operation_id)


def request_cancel(operation_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """Попросить остановить скачивание. Возвращает (операция, ошибка).

    Если операцией не владеет ни один поток (осталась от перезапуска панели),
    ждать некого — она закрывается сразу, а недокачанный файл удаляется."""
    operation = read_operation(operation_id)
    if not operation:
        return None, "Задача не найдена"
    if operation.get("type") != "download":
        return operation, "Отменить можно только скачивание"
    if operation.get("status") not in ("queued", "running"):
        return operation, "Скачивание уже завершено"
    operation.pop("log_tail", None)
    with STATE_LOCK:
        live = operation_id in LIVE_DOWNLOADS
        if live:
            CANCEL_REQUESTS.add(operation_id)
    if not live:
        if operation.get("target_path"):
            part_path(Path(operation["target_path"])).unlink(missing_ok=True)
        append_operation_log(operation_id, "Пользователь отменил скачивание. Поток загрузки не найден (панель перезапускалась), недокачанный файл удалён.")
        finish_operation(operation, False, "Скачивание отменено", cancelled=True, cancel_requested=True)
        return read_operation(operation_id) or operation, None
    update_operation(operation, cancel_requested=True, message="Останавливаем скачивание…")
    append_operation_log(operation_id, "Пользователь попросил отменить скачивание.")
    return operation, None


# ---------------------------------------------------------------- download
def start_download(url: str, kind: str, *, title: str | None = None, sums_url: str | None = None, algo: str | None = None, catalog_id: str | None = None) -> dict[str, Any]:
    """Проверить ссылку и запустить фоновое скачивание. Бросает DownloadError."""
    if kind not in KINDS:
        raise DownloadError("Неизвестный тип образа")
    url = validate_url(url)
    filename = filename_from_url(url, kind)
    target = target_path(kind, filename)
    if sums_url:
        validate_url(sums_url)
        if algo not in hashlib.algorithms_available:
            raise DownloadError("Неизвестный алгоритм контрольной суммы")
    with START_LOCK:
        # Образ мог остаться под другим именем: распакованный .img или qcow2 после конвертации.
        existing = next((name for name in image_candidates(filename) if (target.parent / name).exists()), None)
        if existing:
            raise DownloadError(f"Файл {existing} уже есть на сервере. Удалите его, если нужно скачать заново")
        if any(op.get("target_path") == str(target) for op in unfinished_downloads()):
            raise DownloadError(f"Файл {filename} уже скачивается")
        # Владельца у .part нет (проверили выше) — это остаток прерванной загрузки.
        part_path(target).unlink(missing_ok=True)
        ok, free = free_space_ok(target.parent, None)
        if not ok:
            raise DownloadError(f"На сервере мало места: свободно {human_size(free)}")
        operation = new_operation(
            "download", title or f"Скачивание {filename}", message="Подключаемся к серверу…",
            download_kind=kind, url=url, filename=filename, target_path=str(target), source_path=str(target), page=KINDS[kind]["page"],
            sums_url=sums_url, algo=algo, catalog_id=catalog_id, total_bytes=None, downloaded_bytes=0, cancel_requested=False,
        )
        with STATE_LOCK:
            LIVE_DOWNLOADS[operation["id"]] = str(target)
    append_operation_log(operation["id"], f"Ссылка: {url}")
    append_operation_log(operation["id"], f"Файл: {target}")
    run_operation(operation, download_worker)
    return operation


def open_url(url: str) -> Any:
    request = urllib.request.Request(encode_url(url), headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    return urllib.request.urlopen(request, timeout=TIMEOUT)  # noqa: S310 — схема проверена в validate_url


def describe_network_error(exc: Exception) -> str:
    """Ошибка сети словами для пользователя; исходный текст остаётся в журнале операции."""
    if isinstance(exc, urllib.error.HTTPError):
        return HTTP_ERRORS.get(exc.code, f"Сервер ответил ошибкой {exc.code}")
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        text = str(reason).lower()
        if isinstance(reason, TimeoutError) or "timed out" in text:
            return f"Сервер не отвечает (ждали {TIMEOUT} с)"
        if isinstance(reason, socket.gaierror) or "name or service not known" in text or "nodename nor servname" in text or "name resolution" in text:
            return "Не удалось найти сервер по этой ссылке — проверьте адрес"
        if isinstance(reason, ConnectionRefusedError) or "connection refused" in text:
            return "Сервер отказал в подключении"
        if isinstance(reason, ssl.SSLError) or "certificate" in text or "ssl" in text:
            return "У сервера проблема с сертификатом безопасности"
        if isinstance(reason, UnicodeError):
            return "Ссылка содержит символы, которые сервер не понимает"
        return f"Не удалось подключиться к серверу: {reason}"
    if isinstance(exc, UnicodeError):
        return "Ссылка содержит символы, которые сервер не понимает"
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
        return f"Сервер перестал отвечать (ждали {TIMEOUT} с)"
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return "Сервер разорвал соединение"
    return str(exc)[:240]


def fetch_expected_checksum(sums_url: str, filename: str, operation_id: str) -> str:
    append_operation_log(operation_id, f"Скачиваем контрольные суммы: {sums_url}")
    try:
        with open_url(sums_url) as response:
            text = response.read(SUMS_MAX_BYTES).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        append_operation_log(operation_id, f"Ошибка: {exc}")
        raise DownloadError(f"Не удалось скачать файл контрольных сумм: {describe_network_error(exc)}") from exc
    expected = parse_checksums(text, filename)
    if not expected:
        raise DownloadError(f"В файле контрольных сумм нет записи для {filename}")
    return expected


def download_worker(operation: dict[str, Any]) -> None:
    try:
        run_download(operation)
    finally:
        release_download(operation["id"])


def run_download(operation: dict[str, Any]) -> None:
    operation_id = operation["id"]
    kind = operation["download_kind"]
    target = Path(operation["target_path"])
    part = part_path(target)
    verify = bool(operation.get("sums_url"))
    download_share = 96 if verify else 100

    def cancelled() -> bool:
        return cancel_requested(operation_id)

    def check_cancel(*_args: Any) -> None:
        if cancelled():
            raise DownloadCancelled

    def fail(message: str, *, keep_part: bool = False, **meta: Any) -> None:
        if not keep_part:
            part.unlink(missing_ok=True)
        finish_operation(operation, False, message, **meta)

    def stop(note: str = "Скачивание остановлено, недокачанный файл удалён.") -> None:
        append_operation_log(operation_id, note)
        fail("Скачивание отменено", cancelled=True, cancel_requested=True)

    def network_failure(prefix: str, exc: Exception) -> None:
        append_operation_log(operation_id, f"Ошибка: {exc}")
        fail(f"{prefix}{describe_network_error(exc)}")

    if cancelled():
        stop()
        return
    try:
        response = open_url(operation["url"])
    except Exception as exc:  # noqa: BLE001
        network_failure("", exc)
        return

    with response:
        final_url = response.geturl()
        if final_url != encode_url(operation["url"]):
            append_operation_log(operation_id, f"Перенаправление: {final_url}")
        length = response.headers.get("Content-Length")
        total = int(length) if length and length.isdigit() else None
        ok, free = free_space_ok(target.parent, total)
        if not ok:
            need = f"нужно {human_size((total or 0) + RESERVE_BYTES)}, " if total else ""
            fail(f"На сервере мало места: {need}свободно {human_size(free)}")
            return
        append_operation_log(operation_id, f"Размер файла: {human_size(total) if total else 'неизвестен'}")
        update_operation(operation, total_bytes=total, message=progress_message(0, total, None))

        done = 0
        started = last_tick = time.monotonic()
        last_done = 0
        speed: float | None = None
        try:
            # Файл создаётся монопольно: если .part уже пишет другой поток, вторая загрузка
            # не должна ни писать в него, ни удалять его при ошибке.
            with part.open("xb") as handle:
                while True:
                    if cancelled():
                        stop()
                        return
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last_tick >= 1.0:
                        # Раз в секунду перечитываем JSON операции: так запись прогресса не затирает
                        # флаг cancel_requested, который выставил request_cancel, а отмена из другого
                        # процесса панели (несколько воркеров uvicorn) тоже будет замечена.
                        fresh = read_operation(operation_id) or {}
                        fresh.pop("log_tail", None)
                        operation.update(fresh)
                        if cancelled() or operation.get("cancel_requested"):
                            stop()
                            return
                        instant = (done - last_done) / (now - last_tick)
                        speed = instant if speed is None else speed * 0.7 + instant * 0.3
                        last_tick, last_done = now, done
                        progress = int(done * download_share / total) if total else 0
                        if not total:
                            ok, free = free_space_ok(target.parent, None)
                            if not ok:
                                fail(f"На сервере закончилось место: свободно {human_size(free)}")
                                return
                        update_operation(operation, progress=min(progress, download_share), downloaded_bytes=done, speed_bps=int(speed), message=progress_message(done, total, speed))
        except FileExistsError:
            append_operation_log(operation_id, f"Файл {part.name} уже создан другой загрузкой — эта копия остановлена, чужой файл не тронут.")
            fail(f"Файл {target.name} уже скачивается", keep_part=True)
            return
        except Exception as exc:  # noqa: BLE001
            network_failure("Загрузка прервалась: ", exc)
            return

    if total is not None and done != total:
        fail(f"Файл скачался не полностью: получено {human_size(done)} из {human_size(total)}")
        return
    elapsed = max(time.monotonic() - started, 0.001)
    append_operation_log(operation_id, f"Скачано {human_size(done)} за {human_eta(elapsed) or '1 с'} ({human_size(done / elapsed)}/с).")
    update_operation(operation, progress=download_share, downloaded_bytes=done, total_bytes=done if total is None else total, message="Файл скачан, проверяем…")

    try:
        if verify:
            expected = fetch_expected_checksum(operation["sums_url"], operation["filename"], operation_id)
            check_cancel()
            algo = operation.get("algo") or "sha256"
            update_operation(operation, progress=97, message=f"Проверяем контрольную сумму ({algo})…")
            actual = file_digest(part, algo, on_progress=check_cancel)
            if actual != expected:
                append_operation_log(operation_id, f"Ожидали {algo}: {expected}")
                append_operation_log(operation_id, f"Получили {algo}: {actual}")
                fail("Контрольная сумма не совпала — файл повреждён или подменён, поэтому удалён. Попробуйте скачать ещё раз")
                return
            append_operation_log(operation_id, f"Контрольная сумма {algo} совпала.")
        check_cancel()
    except DownloadCancelled:
        stop("Скачивание остановлено во время проверки, файл удалён.")
        return
    except DownloadError as exc:
        fail(str(exc))
        return

    if target.exists():
        fail(f"Файл {target.name} появился на сервере, пока шло скачивание. Скачанная копия удалена")
        return
    part.replace(target)
    append_operation_log(operation_id, f"Файл сохранён: {target}")
    try:
        result = after_download(operation, target, kind, cancelled)
    except DownloadCancelled:
        append_operation_log(operation_id, "Распаковка остановлена, скачанный архив и недораспакованный файл удалены.")
        finish_operation(operation, False, "Скачивание отменено", cancelled=True, cancel_requested=True)
        return
    except DownloadError as exc:
        finish_operation(operation, False, str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        finish_operation(operation, False, f"Файл скачан, но не удалось его подготовить: {str(exc)[:200]}", result_path=str(target))
        return
    finish_operation(operation, True, result["message"], **result["meta"])


def after_download(operation: dict[str, Any], target: Path, kind: str, should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Что делать со скачанным файлом: обновить пул ISO либо распаковать и конвертировать диск."""
    operation_id = operation["id"]
    if kind == "iso":
        refresh = run_cmd(["virsh", "pool-refresh", ISO_POOL], timeout=20)
        append_operation_log(operation_id, "Список установочных образов обновлён." if refresh["ok"] else f"Не удалось обновить пул {ISO_POOL}: {refresh.get('stderr') or refresh.get('stdout')}")
        return {"message": f"Готово: {target.name} — можно создавать машину", "meta": {"result_path": str(target)}}

    path = target
    if target.name.lower().endswith(".img.xz"):
        update_operation(operation, progress=98, message=f"Распаковываем {target.name}…")
        append_operation_log(operation_id, "Распаковываем сжатый образ…")
        try:
            path = extract_xz_image(target, should_stop)
        except DownloadCancelled:
            target.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001
            # Битый архив со страницы не видно и не удалить (там только .img/.raw/.qcow2),
            # а повторное скачивание он бы блокировал — убираем сами.
            target.unlink(missing_ok=True)
            append_operation_log(operation_id, f"Ошибка распаковки: {exc}")
            raise DownloadError("Не удалось распаковать скачанный архив — файл повреждён и удалён. Попробуйте скачать ещё раз") from exc
        target.unlink(missing_ok=True)
        append_operation_log(operation_id, f"Распакован: {path}")
    convert = start_convert(path)
    meta = {"result_path": str(path)}
    if convert:
        meta["convert_operation_id"] = convert["id"]
        append_operation_log(operation_id, f"Запущена конвертация в qcow2, задача {convert['id']}.")
        return {"message": f"Готово: {path.name} скачан, конвертируем в формат qcow2", "meta": meta}
    return {"message": f"Готово: {path.name} — можно создавать машину", "meta": meta}


def extract_xz_image(compressed: Path, should_stop: Callable[[], bool] | None = None) -> Path:
    """Распаковать .img.xz рядом с архивом (имя подбирает app, как при загрузке с компьютера).
    Между кусками проверяет should_stop: при отмене бросает DownloadCancelled, недораспакованный файл удаляется."""
    import app  # локальный импорт: app.py подключает этот модуль в самом конце

    extracted = app.unique_disk_image_path(compressed.name[:-3])
    try:
        with lzma.open(compressed, "rb") as source, extracted.open("wb") as handle:
            while True:
                if should_stop and should_stop():
                    raise DownloadCancelled
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
    except BaseException:
        extracted.unlink(missing_ok=True)
        raise
    return extracted


def start_convert(path: Path) -> dict[str, Any] | None:
    import app  # локальный импорт: app.py подключает этот модуль в самом конце

    return app.start_disk_convert_operation(path)


# ---------------------------------------------------------------- routes
def page_for(kind: str) -> str:
    return KINDS.get(kind, KINDS["iso"])["page"]


@router.post("/downloads/url")
def download_from_url(request: Request, url: str = Form(""), kind: str = Form("iso")):
    auth = require_auth(request)
    if auth:
        return auth
    kind = kind if kind in KINDS else "iso"
    try:
        operation = start_download(url, kind)
    except DownloadError as exc:
        return redirect_with_message(page_for(kind), "download_error", str(exc))
    return redirect_with_message(page_for(kind), "download_message", f"Скачиваем {operation['filename']} — ход загрузки виден выше")


@router.post("/downloads/catalog/{image_id}")
def download_from_catalog(request: Request, image_id: str):
    auth = require_auth(request)
    if auth:
        return auth
    entry = catalog.cloud_image(image_id)
    if not entry:
        return redirect_with_message("/disk-images", "download_error", "Такого образа нет в каталоге")
    arch = host_arch()
    source = entry.get("arches", {}).get(arch)
    if not source:
        return redirect_with_message("/disk-images", "download_error", f"У {entry['title']} нет версии для этого сервера ({arch})")
    try:
        operation = start_download(source["url"], "disk", title=f"Скачивание {entry['title']}", sums_url=source.get("sums"), algo=source.get("algo"), catalog_id=image_id)
    except DownloadError as exc:
        return redirect_with_message("/disk-images", "download_error", str(exc))
    return redirect_with_message("/disk-images", "download_message", f"Скачиваем {entry['title']} ({operation['filename']}) — после загрузки проверим контрольную сумму")


@router.post("/downloads/{operation_id}/cancel")
def cancel_download(request: Request, operation_id: str):
    auth = require_auth(request)
    if auth:
        return auth
    operation, error = request_cancel(operation_id)
    page = page_for(str(operation.get("download_kind"))) if operation else "/iso"
    if error:
        return redirect_with_message(page, "download_error", error)
    filename = operation.get("filename", "")
    if operation.get("status") == "error":
        # Потока не было — операция уже закрыта, ждать нечего.
        return redirect_with_message(page, "download_message", " ".join(word for word in ("Скачивание", filename, "отменено") if word))
    return redirect_with_message(page, "download_message", f"Останавливаем скачивание {filename}".rstrip())


@router.post("/api/operations/{operation_id}/cancel")
def api_cancel_operation(request: Request, operation_id: str):
    auth = require_api_auth(request)
    if auth:
        return auth
    operation, error = request_cancel(operation_id)
    if operation is None:
        return JSONResponse({"ok": False, "error": error}, status_code=404)
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400 if operation.get("type") != "download" else 409)
    return {"ok": True, "operation": read_operation(operation_id) or operation}


@router.on_event("startup")
def close_stale_downloads() -> None:
    """После перезапуска панели потоков загрузки нет: закрываем такие операции и убираем их .part."""
    reap_stale_downloads()


# ---------------------------------------------------------------- templates
def register_template_globals() -> None:
    """Функция downloads_panel нужна шаблонам iso.html и disk_images.html, которые
    рендерит app.py своим окружением Jinja; регистрируем её и в core, и в app.

    Импорт app здесь циклический, но безопасный: app.py подключает этот модуль
    в самом конце, когда его `templates` уже создан, а наш `router` — уже определён."""
    import app

    for env in {id(item): item for item in (core.templates.env, app.templates.env)}.values():
        env.globals["downloads_panel"] = downloads_panel


register_template_globals()
