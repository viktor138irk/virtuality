"""Скачивание образов по ссылке и из каталога: парсер контрольных сумм,
проверка ссылок, фоновая загрузка с локального http-сервера, отмена."""
import hashlib
import lzma
import socket
import ssl
import threading
import time
import urllib.error
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

import app  # noqa: F401 — подключает features.downloads к приложению
import catalog
import core
import features.downloads as downloads

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MB = 1024 * 1024
SLOW_TOTAL = 4 * MB
CHUNKED_TOTAL = 40 * 64 * 1024


class Handler(SimpleHTTPRequestHandler):
    """Раздаёт файлы из каталога www; /slow.iso отдаёт 4 МБ по 64 КБ с паузами, /redirect.iso — перенаправляет,
    /chunked.iso — 2,5 МБ с паузами без заголовка Content-Length (размер заранее неизвестен)."""

    root = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=self.root, **kwargs)

    def log_message(self, *args):  # noqa: D102 — тише в тестах
        pass

    def do_GET(self):
        if self.path == "/redirect.iso":
            self.send_response(302)
            self.send_header("Location", "/small.iso")
            self.end_headers()
            return
        if self.path in ("/slow.iso", "/chunked.iso"):
            total = SLOW_TOTAL if self.path == "/slow.iso" else CHUNKED_TOTAL
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            if self.path == "/slow.iso":
                self.send_header("Content-Length", str(total))
            self.end_headers()
            try:
                for _ in range(total // (64 * 1024)):
                    self.wfile.write(b"\0" * 64 * 1024)
                    self.wfile.flush()
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        super().do_GET()


@pytest.fixture()
def www(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    (root / "small.iso").write_bytes(b"ISO" * 5000)
    handler = type("TestHandler", (Handler,), {"root": str(root)})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(root=root, url=f"http://127.0.0.1:{server.server_port}")
    server.shutdown()
    server.server_close()


def wait_for(operation_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        operation = core.read_operation(operation_id)
        if operation and operation.get("status") in ("success", "error"):
            return operation
        time.sleep(0.05)
    raise AssertionError("операция не завершилась вовремя")


def wait_until(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("условие не наступило вовремя")


def message_of(operation_id: str) -> str:
    return str((core.read_operation(operation_id) or {}).get("message", ""))


def seed_running_download(kind: str, filename: str, **meta) -> dict:
    """Операция «скачивается» без потока — как после перезапуска панели."""
    target = downloads.target_path(kind, filename)
    operation = core.new_operation("download", f"Скачивание {filename}", download_kind=kind, url=f"http://127.0.0.1:9/{filename}", filename=filename, target_path=str(target), cancel_requested=False, **meta)
    core.update_operation(operation, status="running")
    return operation


# ---------------------------------------------------------------- parsers & formatting
def test_parse_checksums_ubuntu_star_format():
    text = (FIXTURES / "SHA256SUMS").read_text()
    assert downloads.parse_checksums(text, "resolute-server-cloudimg-amd64.img") == "5861314d7fccb39c2192173240eab44fa35ca66426201ca2acd0630a6258dd51"
    assert downloads.parse_checksums(text, "resolute-server-cloudimg-arm64.img").startswith("f6916295")
    assert downloads.parse_checksums(text, "missing.img") is None


def test_parse_checksums_debian_two_spaces_and_comments():
    text = (FIXTURES / "SHA512SUMS").read_text()
    assert downloads.parse_checksums(text, "debian-13-genericcloud-amd64.qcow2").startswith("1412d62c2d55")
    assert downloads.parse_checksums(text, "debian-13-genericcloud-arm64.qcow2").startswith("27c03aab0b5e")


def test_parse_checksums_alpine_bare_hash_and_bsd_style():
    bare = (FIXTURES / "nocloud_alpine-3.23.0-x86_64-uefi-cloudinit-r0.qcow2.sha512").read_text()
    assert downloads.parse_checksums(bare, "anything.qcow2") == bare.strip().lower()
    bsd = "SHA256 (disk.qcow2) = ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789\n"
    assert downloads.parse_checksums(bsd, "disk.qcow2") == "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    assert downloads.parse_checksums(bsd, "other.qcow2") is None
    assert downloads.parse_checksums("not a checksum file\n", "disk.qcow2") is None


def test_human_size_and_eta():
    assert downloads.human_size(0) == "0 Б"
    assert downloads.human_size(1023) == "1023 Б"
    assert downloads.human_size(1536) == "1,5 КБ"
    assert downloads.human_size(245 * MB) == "245 МБ"
    assert downloads.human_size(int(3.1 * 1024 * MB)) == "3,1 ГБ"
    assert downloads.human_size(10 * MB) == "10 МБ"
    assert downloads.human_eta(45) == "45 с"
    assert downloads.human_eta(244) == "4 мин"
    assert downloads.human_eta(3700) == "1 ч 2 мин"
    assert downloads.human_eta(7200) == "2 ч"


def test_progress_message():
    assert downloads.progress_message(245 * MB, int(3.1 * 1024 * MB), 12 * MB) == "245 МБ из 3,1 ГБ · 12 МБ/с · осталось 4 мин"
    assert downloads.progress_message(245 * MB, None, 12 * MB) == "245 МБ · 12 МБ/с"
    assert downloads.progress_message(0, 100 * MB, None) == "0 Б из 100 МБ"


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("url", ["", "ftp://example.org/x.iso", "file:///etc/passwd", "javascript:alert(1)", "http://", "http:///x.iso", "http://example.org/a b.iso", "http://example.org:abc/x.iso", "http://[::1/x.iso"])
def test_validate_url_rejects(url):
    with pytest.raises(downloads.DownloadError):
        downloads.validate_url(url)


def test_encode_url_handles_cyrillic_and_keeps_encoded_links():
    encoded = downloads.encode_url("http://пример.рф:8080/папка/образ диска.iso?имя=ы#x")
    assert encoded == "http://xn--e1afmkfd.xn--p1ai:8080/%D0%BF%D0%B0%D0%BF%D0%BA%D0%B0/%D0%BE%D0%B1%D1%80%D0%B0%D0%B7%20%D0%B4%D0%B8%D1%81%D0%BA%D0%B0.iso?%D0%B8%D0%BC%D1%8F=%D1%8B"
    assert downloads.encode_url(encoded) == encoded
    for same in ("https://user:pw@h.example/a%20b/x.iso?a=1&b=%2F", "http://[::1]:9/x.iso", "http://127.0.0.1/x.iso"):
        assert downloads.encode_url(same) == same
    assert downloads.validate_url("https://пример.рф/образ.iso") == "https://пример.рф/образ.iso"


def test_describe_network_error_is_friendly():
    describe = downloads.describe_network_error
    assert describe(urllib.error.HTTPError("u", 404, "Not Found", {}, None)) == "Файл по ссылке не найден (ошибка 404)"
    assert describe(urllib.error.HTTPError("u", 403, "Forbidden", {}, None)) == "Сервер запретил скачивание (ошибка 403)"
    assert describe(urllib.error.HTTPError("u", 418, "Teapot", {}, None)) == "Сервер ответил ошибкой 418"
    assert describe(urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))) == "Не удалось найти сервер по этой ссылке — проверьте адрес"
    assert describe(urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))) == "Сервер отказал в подключении"
    assert describe(urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))) == "У сервера проблема с сертификатом безопасности"
    assert describe(urllib.error.URLError(TimeoutError())).startswith("Сервер не отвечает")
    assert describe(UnicodeEncodeError("ascii", "образ", 0, 5, "ordinal not in range(128)")) == "Ссылка содержит символы, которые сервер не понимает"
    assert describe(ConnectionResetError(104, "reset")) == "Сервер разорвал соединение"
    assert describe(RuntimeError("boom")) == "boom"


def test_filename_from_url_sanitises_and_checks_suffix():
    assert downloads.filename_from_url("https://h.example/a%20b%20(1).ISO?x=1#frag", "iso") == "a-b-_1.iso"
    assert downloads.filename_from_url("https://h.example/dir/debian-13.qcow2", "disk") == "debian-13.qcow2"
    assert downloads.filename_from_url("https://h.example/rpi/2026-01-01-raspios.img.xz", "disk") == "2026-01-01-raspios.img.xz"
    assert downloads.filename_from_url("https://h.example/../../etc.iso", "iso") == "etc.iso"
    for url, kind in (("https://h.example/", "iso"), ("https://h.example/setup.exe", "iso"), ("https://h.example/x.iso", "disk"), ("https://h.example/x.qcow2", "iso")):
        with pytest.raises(downloads.DownloadError):
            downloads.filename_from_url(url, kind)


def test_start_download_refuses_existing_file(data_dirs):
    (data_dirs["iso"] / "small.iso").write_bytes(b"x")
    with pytest.raises(downloads.DownloadError, match="уже есть"):
        downloads.start_download("http://127.0.0.1:9/small.iso", "iso")


def test_start_download_refuses_when_extracted_image_exists(data_dirs):
    # Из pi.img.xz после скачивания остаётся pi.img (или pi.qcow2 после конвертации) — это тот же образ.
    (data_dirs["disk_images"] / "pi.img").write_bytes(b"x")
    with pytest.raises(downloads.DownloadError, match="pi.img уже есть"):
        downloads.start_download("http://127.0.0.1:9/pi.img.xz", "disk")
    (data_dirs["disk_images"] / "pi.img").unlink()
    (data_dirs["disk_images"] / "pi.qcow2").write_bytes(b"x")
    with pytest.raises(downloads.DownloadError, match="pi.qcow2 уже есть"):
        downloads.start_download("http://127.0.0.1:9/pi.img", "disk")


def test_start_download_refuses_when_already_downloading(data_dirs, monkeypatch):
    seed_running_download("iso", "small.iso")
    with pytest.raises(downloads.DownloadError, match="уже скачивается"):
        downloads.start_download("http://127.0.0.1:9/small.iso", "iso")
    # проверка идёт по всем операциям, а не по последним 50
    for index in range(60):
        core.new_operation("vm_create", f"Создание VM {index}")
    with pytest.raises(downloads.DownloadError, match="уже скачивается"):
        downloads.start_download("http://127.0.0.1:9/small.iso", "iso")


def test_start_download_checks_free_space(data_dirs, monkeypatch):
    monkeypatch.setattr(downloads.shutil, "disk_usage", lambda path: SimpleNamespace(free=1000, total=1000, used=0))
    with pytest.raises(downloads.DownloadError, match="мало места"):
        downloads.start_download("http://127.0.0.1:9/small.iso", "iso")


def test_image_candidates():
    assert downloads.image_candidates("a.img.xz") == ["a.img.xz", "a.img", "a.qcow2"]
    assert downloads.image_candidates("a.img") == ["a.img", "a.qcow2"]
    assert downloads.image_candidates("a.qcow2") == ["a.qcow2"]


# ---------------------------------------------------------------- background download
def test_download_iso_success_refreshes_pool(data_dirs, www, monkeypatch):
    calls = []
    monkeypatch.setattr(downloads, "run_cmd", lambda cmd, timeout=12: calls.append(cmd) or {"ok": True, "stdout": "", "stderr": ""})
    operation = downloads.start_download(f"{www.url}/small.iso", "iso")
    assert operation["type"] == "download" and operation["download_kind"] == "iso"
    done = wait_for(operation["id"])
    assert done["status"] == "success", done
    assert done["progress"] == 100
    assert done["total_bytes"] == 15000 and done["downloaded_bytes"] == 15000
    assert (data_dirs["iso"] / "small.iso").read_bytes() == b"ISO" * 5000
    assert not (data_dirs["iso"] / ".small.iso.part").exists()
    assert ["virsh", "pool-refresh", "virtuality-iso"] in calls
    assert "Готово" in done["message"]
    assert not downloads.is_live(operation["id"])


def test_download_follows_redirect(data_dirs, www):
    operation = downloads.start_download(f"{www.url}/redirect.iso", "iso")
    done = wait_for(operation["id"])
    assert done["status"] == "success", done
    assert (data_dirs["iso"] / "redirect.iso").stat().st_size == 15000
    assert "Перенаправление" in done["log_tail"]


def test_download_with_cyrillic_file_name_in_url(data_dirs, www):
    (www.root / "образ.iso").write_bytes(b"cyrillic" * 10)
    operation = downloads.start_download(f"{www.url}/образ.iso", "iso")
    assert operation["filename"] == "virtuality-iso.iso"
    done = wait_for(operation["id"])
    assert done["status"] == "success", done
    assert (data_dirs["iso"] / "virtuality-iso.iso").read_bytes() == b"cyrillic" * 10
    assert "Перенаправление" not in done["log_tail"]


def test_download_missing_file_fails(data_dirs, www):
    operation = downloads.start_download(f"{www.url}/nope.iso", "iso")
    done = wait_for(operation["id"])
    assert done["status"] == "error"
    assert done["message"] == "Файл по ссылке не найден (ошибка 404)"
    assert "HTTP Error 404" in done["log_tail"]  # исходный текст остаётся в журнале
    assert not list(data_dirs["iso"].iterdir())


def test_download_refuses_when_disk_fills_up(data_dirs, www, monkeypatch):
    real = downloads.free_space_ok
    seen = []

    def fake(directory, needed):
        seen.append(needed)
        return real(directory, needed) if len(seen) == 1 else (False, 42)

    monkeypatch.setattr(downloads, "free_space_ok", fake)
    operation = downloads.start_download(f"{www.url}/small.iso", "iso")
    done = wait_for(operation["id"])
    assert done["status"] == "error"
    assert "мало места" in done["message"] and "42 Б" in done["message"]
    assert not list(data_dirs["iso"].iterdir())


def test_download_without_content_length_stops_when_disk_fills_up(data_dirs, www, monkeypatch):
    real = downloads.free_space_ok
    seen = []

    def fake(directory, needed):
        seen.append(needed)
        return real(directory, needed) if len(seen) < 3 else (False, 42)

    monkeypatch.setattr(downloads, "free_space_ok", fake)
    operation = downloads.start_download(f"{www.url}/chunked.iso", "iso")
    done = wait_for(operation["id"])
    assert done["status"] == "error"
    assert "закончилось место" in done["message"] and "42 Б" in done["message"]
    assert done["total_bytes"] is None and seen == [None, None, None]
    assert not (data_dirs["iso"] / ".chunked.iso.part").exists()
    assert not list(data_dirs["iso"].iterdir())


def test_download_cancel(data_dirs, www, logged_in):
    operation = downloads.start_download(f"{www.url}/slow.iso", "iso")
    wait_until(lambda: (core.read_operation(operation["id"]) or {}).get("status") == "running")
    wait_until(lambda: (data_dirs["iso"] / ".slow.iso.part").exists())
    response = logged_in.post(f"/api/operations/{operation['id']}/cancel")
    assert response.status_code == 200 and response.json()["ok"] is True
    done = wait_for(operation["id"])
    assert done["status"] == "error"
    assert done["message"] == "Скачивание отменено"
    assert done["cancelled"] is True and done["cancel_requested"] is True
    assert not (data_dirs["iso"] / ".slow.iso.part").exists()
    assert not (data_dirs["iso"] / "slow.iso").exists()
    # The worker thread releases its markers right after it finishes the operation.
    wait_until(lambda: not downloads.cancel_requested(operation["id"]) and not downloads.is_live(operation["id"]))


def test_cancel_flag_on_disk_is_honoured_and_not_overwritten(data_dirs, www):
    # Отмену могла принять другая копия панели: поток видит флаг в JSON операции раз в секунду.
    operation = downloads.start_download(f"{www.url}/slow.iso", "iso")
    wait_until(lambda: (data_dirs["iso"] / ".slow.iso.part").exists())
    fresh = core.read_operation(operation["id"])
    fresh.pop("log_tail", None)
    core.update_operation(fresh, cancel_requested=True, message="Останавливаем скачивание…")
    done = wait_for(operation["id"])
    assert done["status"] == "error" and done["cancelled"] is True and done["cancel_requested"] is True
    assert not (data_dirs["iso"] / ".slow.iso.part").exists()


def test_second_download_of_same_file_does_not_touch_the_first(data_dirs, www):
    first = downloads.start_download(f"{www.url}/slow.iso", "iso")
    part = data_dirs["iso"] / ".slow.iso.part"
    wait_until(part.exists)
    # Второй запуск, проскочивший проверку «уже скачивается» (например, из другого процесса): операцию создаём руками.
    second = core.new_operation("download", "Скачивание slow.iso", download_kind="iso", url=f"{www.url}/slow.iso", filename="slow.iso", target_path=first["target_path"], cancel_requested=False)
    core.update_operation(second, status="running")
    downloads.download_worker(second)
    done = core.read_operation(second["id"])
    assert done["status"] == "error" and "уже скачивается" in done["message"]
    assert part.exists()  # чужой файл не тронут
    assert wait_for(first["id"])["status"] == "success"
    assert (data_dirs["iso"] / "slow.iso").stat().st_size == SLOW_TOTAL


def test_leftover_part_file_without_owner_does_not_block_download(data_dirs, www):
    part = data_dirs["iso"] / ".small.iso.part"
    part.write_bytes(b"half of a crashed download")
    operation = downloads.start_download(f"{www.url}/small.iso", "iso")
    done = wait_for(operation["id"])
    assert done["status"] == "success", done
    assert (data_dirs["iso"] / "small.iso").read_bytes() == b"ISO" * 5000
    assert not part.exists()


def test_stale_downloads_are_reaped_on_startup(data_dirs, www, monkeypatch):
    stale = seed_running_download("iso", "small.iso")
    part = downloads.part_path(Path(stale["target_path"]))
    part.write_bytes(b"half")
    (data_dirs["disk_images"] / ".orphan.img.part").write_bytes(b"x")
    live = downloads.start_download(f"{www.url}/slow.iso", "iso")
    wait_until(lambda: (data_dirs["iso"] / ".slow.iso.part").exists())

    assert downloads.reap_stale_downloads() == 1
    closed = core.read_operation(stale["id"])
    assert closed["status"] == "error" and closed["interrupted"] is True and "перезапущена" in closed["message"]
    assert not part.exists() and not (data_dirs["disk_images"] / ".orphan.img.part").exists()
    assert (data_dirs["iso"] / ".slow.iso.part").exists()  # живое скачивание не трогаем
    assert wait_for(live["id"])["status"] == "success"
    # после закрытия зависшей операции тот же файл можно скачать снова
    done = wait_for(downloads.start_download(f"{www.url}/small.iso", "iso")["id"])
    assert done["status"] == "success" and (data_dirs["iso"] / "small.iso").read_bytes() == b"ISO" * 5000
    assert downloads.close_stale_downloads() is None


def test_cancel_of_stale_download_finishes_it_immediately(data_dirs, logged_in):
    stale = seed_running_download("disk", "y.qcow2")
    part = downloads.part_path(Path(stale["target_path"]))
    part.write_bytes(b"x")
    response = logged_in.post(f"/api/operations/{stale['id']}/cancel")
    operation = response.json()["operation"]
    assert response.status_code == 200 and operation["status"] == "error" and operation["cancelled"] is True
    assert operation["message"] == "Скачивание отменено" and operation["cancel_requested"] is True
    assert not part.exists()
    assert not downloads.cancel_requested(stale["id"])
    assert logged_in.post(f"/api/operations/{stale['id']}/cancel").status_code == 409


def test_catalog_download_verifies_checksum(data_dirs, www):
    payload = b"QCOW2 image bytes" * 100
    (www.root / "disk.qcow2").write_bytes(payload)
    (www.root / "SHA256SUMS").write_text(f"{hashlib.sha256(payload).hexdigest()} *disk.qcow2\n")
    operation = downloads.start_download(f"{www.url}/disk.qcow2", "disk", sums_url=f"{www.url}/SHA256SUMS", algo="sha256", catalog_id="test")
    done = wait_for(operation["id"])
    assert done["status"] == "success", done
    assert "совпала" in done["log_tail"]
    assert (data_dirs["disk_images"] / "disk.qcow2").read_bytes() == payload


def test_catalog_download_checksum_mismatch_deletes_file(data_dirs, www):
    (www.root / "disk.qcow2").write_bytes(b"corrupted" * 100)
    (www.root / "SHA512SUMS").write_text(f"{'0' * 128}  disk.qcow2\n")
    operation = downloads.start_download(f"{www.url}/disk.qcow2", "disk", sums_url=f"{www.url}/SHA512SUMS", algo="sha512")
    done = wait_for(operation["id"])
    assert done["status"] == "error"
    assert "Контрольная сумма не совпала" in done["message"]
    assert not list(data_dirs["disk_images"].iterdir())


def test_catalog_download_fails_without_sums_entry(data_dirs, www):
    (www.root / "disk.qcow2").write_bytes(b"data")
    (www.root / "SHA256SUMS").write_text(f"{'a' * 64} *other.qcow2\n")
    operation = downloads.start_download(f"{www.url}/disk.qcow2", "disk", sums_url=f"{www.url}/SHA256SUMS", algo="sha256")
    done = wait_for(operation["id"])
    assert done["status"] == "error"
    assert "нет записи" in done["message"]
    assert not list(data_dirs["disk_images"].iterdir())


def test_cancel_during_checksum_verification_removes_file(data_dirs, www, logged_in, monkeypatch):
    payload = b"QCOW2 image bytes" * 100
    (www.root / "disk.qcow2").write_bytes(payload)
    (www.root / "SHA256SUMS").write_text(f"{hashlib.sha256(payload).hexdigest()} *disk.qcow2\n")

    def slow_digest(path, algo, on_progress=None):
        for _ in range(200):
            time.sleep(0.05)
            if on_progress:
                on_progress(1)
        return hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(downloads, "file_digest", slow_digest)
    operation = downloads.start_download(f"{www.url}/disk.qcow2", "disk", sums_url=f"{www.url}/SHA256SUMS", algo="sha256")
    wait_until(lambda: "Проверяем контрольную сумму" in message_of(operation["id"]))
    assert logged_in.post(f"/api/operations/{operation['id']}/cancel").status_code == 200
    done = wait_for(operation["id"])
    assert done["status"] == "error" and done["cancelled"] is True and done["message"] == "Скачивание отменено"
    assert "во время проверки" in done["log_tail"]
    assert not list(data_dirs["disk_images"].iterdir())


def test_disk_download_extracts_xz_and_starts_conversion(data_dirs, www, monkeypatch):
    raw = bytes(range(256)) * 256
    (www.root / "pi.img.xz").write_bytes(lzma.compress(raw))
    started = []
    monkeypatch.setattr(app, "start_disk_convert_operation", lambda path: started.append(path) or {"id": "conv-1"})
    operation = downloads.start_download(f"{www.url}/pi.img.xz", "disk")
    done = wait_for(operation["id"])
    assert done["status"] == "success", done
    assert (data_dirs["disk_images"] / "pi.img").read_bytes() == raw
    assert not (data_dirs["disk_images"] / "pi.img.xz").exists()
    assert started == [data_dirs["disk_images"] / "pi.img"]
    assert done["convert_operation_id"] == "conv-1"
    assert "qcow2" in done["message"]


def test_cancel_during_unpack_removes_archive_and_partial_image(data_dirs, www, logged_in, monkeypatch):
    raw = bytes(range(256)) * 4096  # 1 МБ, распаковка кусками по 16 КБ с паузой — успеваем отменить
    (www.root / "pi.img.xz").write_bytes(lzma.compress(raw))
    real = downloads.extract_xz_image

    def slow_extract(compressed, should_stop=None):
        def stop():
            time.sleep(0.05)
            return bool(should_stop and should_stop())

        return real(compressed, stop)

    started = []
    monkeypatch.setattr(downloads, "extract_xz_image", slow_extract)
    monkeypatch.setattr(downloads, "CHUNK_SIZE", 16 * 1024)
    monkeypatch.setattr(app, "start_disk_convert_operation", lambda path: started.append(path))
    operation = downloads.start_download(f"{www.url}/pi.img.xz", "disk")
    wait_until(lambda: "Распаковываем" in message_of(operation["id"]))
    assert logged_in.post(f"/api/operations/{operation['id']}/cancel").status_code == 200
    done = wait_for(operation["id"])
    assert done["status"] == "error" and done["cancelled"] is True and done["message"] == "Скачивание отменено"
    assert "Распаковка остановлена" in done["log_tail"]
    assert not list(data_dirs["disk_images"].iterdir()) and started == []


def test_corrupt_xz_archive_is_removed_and_can_be_downloaded_again(data_dirs, www, monkeypatch):
    (www.root / "bad.img.xz").write_bytes(b"definitely not an xz archive" * 100)
    started = []
    monkeypatch.setattr(app, "start_disk_convert_operation", lambda path: started.append(path))
    done = wait_for(downloads.start_download(f"{www.url}/bad.img.xz", "disk")["id"])
    assert done["status"] == "error"
    assert done["message"] == "Не удалось распаковать скачанный архив — файл повреждён и удалён. Попробуйте скачать ещё раз"
    assert "Ошибка распаковки" in done["log_tail"]
    assert not list(data_dirs["disk_images"].iterdir()) and started == []
    # архив убран, поэтому повторное скачивание не упирается в «уже есть на сервере»
    assert wait_for(downloads.start_download(f"{www.url}/bad.img.xz", "disk")["id"])["status"] == "error"


def test_extract_xz_image_stops_and_cleans_up(data_dirs):
    raw = bytes(range(256)) * 1024
    compressed = data_dirs["disk_images"] / "pi.img.xz"
    compressed.write_bytes(lzma.compress(raw))
    assert downloads.extract_xz_image(compressed).read_bytes() == raw
    (data_dirs["disk_images"] / "pi.img").unlink()
    with pytest.raises(downloads.DownloadCancelled):
        downloads.extract_xz_image(compressed, lambda: True)
    assert sorted(item.name for item in data_dirs["disk_images"].iterdir()) == ["pi.img.xz"]


def test_qcow2_download_needs_no_conversion(data_dirs, www, monkeypatch):
    (www.root / "ready.qcow2").write_bytes(b"q" * 10)
    monkeypatch.setattr(app, "start_disk_convert_operation", lambda path: None)
    done = wait_for(downloads.start_download(f"{www.url}/ready.qcow2", "disk")["id"])
    assert done["status"] == "success" and "convert_operation_id" not in done
    assert done["result_path"] == str(data_dirs["disk_images"] / "ready.qcow2")


# ---------------------------------------------------------------- routes
def test_url_route_starts_download(logged_in, data_dirs, www, monkeypatch):
    # The worker is started by hand below: a 15 KB file would otherwise finish before the checks.
    pending = {}
    monkeypatch.setattr(downloads, "run_operation", lambda operation, worker: pending.update(operation=operation, worker=worker))
    response = logged_in.post("/downloads/url", data={"url": f"{www.url}/small.iso", "kind": "iso"}, follow_redirects=False)
    assert response.status_code == 303
    location = unquote(response.headers["location"])
    assert location.startswith("/iso?download_message=") and "small.iso" in location
    running = downloads.active_downloads("iso")
    assert len(running) == 1 and running[0]["filename"] == "small.iso"
    core.run_operation(pending["operation"], pending["worker"])
    assert wait_for(running[0]["id"])["status"] == "success"
    assert (data_dirs["iso"] / "small.iso").exists()


def test_url_route_rejects_bad_links(logged_in, data_dirs):
    response = logged_in.post("/downloads/url", data={"url": "ftp://example.org/x.iso", "kind": "iso"}, follow_redirects=False)
    assert response.status_code == 303
    assert "download_error=" in response.headers["location"] and response.headers["location"].startswith("/iso?")
    response = logged_in.post("/downloads/url", data={"url": "https://example.org/setup.exe", "kind": "disk"}, follow_redirects=False)
    assert response.headers["location"].startswith("/disk-images?download_error=")
    assert not core.running_operations("download") and not downloads.unfinished_downloads()


def test_url_route_requires_login(client):
    assert client.post("/downloads/url", data={"url": "https://example.org/x.iso"}, follow_redirects=False).headers["location"] == "/login"
    assert client.post("/api/operations/x/cancel").status_code == 401


def test_catalog_route(logged_in, data_dirs, www, monkeypatch):
    payload = b"debian" * 1000
    (www.root / "debian-13-genericcloud-amd64.qcow2").write_bytes(payload)
    (www.root / "SHA512SUMS").write_text(f"{hashlib.sha512(payload).hexdigest()}  debian-13-genericcloud-amd64.qcow2\n")
    entry = {"id": "debian-13", "title": "Debian 13", "hint": "x", "login": "debian", "arches": {"x86_64": {"url": f"{www.url}/debian-13-genericcloud-amd64.qcow2", "sums": f"{www.url}/SHA512SUMS", "algo": "sha512"}}}
    monkeypatch.setattr(catalog, "CLOUD_IMAGES", [entry])
    monkeypatch.setattr(downloads, "host_arch", lambda: "x86_64")
    monkeypatch.setattr(app, "start_disk_convert_operation", lambda path: None)

    assert unquote(logged_in.post("/downloads/catalog/nope", follow_redirects=False).headers["location"]).startswith("/disk-images?download_error=Такого образа нет")
    # The worker is started by hand below: a 6 KB file would otherwise finish before the page is rendered.
    pending = {}
    monkeypatch.setattr(downloads, "run_operation", lambda operation, worker: pending.update(operation=operation, worker=worker))
    response = logged_in.post("/downloads/catalog/debian-13", follow_redirects=False)
    assert response.status_code == 303 and "download_message=" in response.headers["location"]
    page = logged_in.get("/disk-images").text
    assert "Скачивается" in page
    running = downloads.active_downloads("disk")
    assert running[0]["catalog_id"] == "debian-13"
    core.run_operation(pending["operation"], pending["worker"])
    assert wait_for(running[0]["id"])["status"] == "success"
    assert (data_dirs["disk_images"] / "debian-13-genericcloud-amd64.qcow2").read_bytes() == payload
    page = logged_in.get("/disk-images").text
    assert "Загружен" in page and "/vm/create?image=debian-13-genericcloud-amd64.qcow2" in page
    # второй раз — файл уже есть
    response = logged_in.post("/downloads/catalog/debian-13", follow_redirects=False)
    assert "download_error=" in response.headers["location"]


def test_catalog_route_skips_missing_arch(logged_in, data_dirs, monkeypatch):
    monkeypatch.setattr(catalog, "CLOUD_IMAGES", [{"id": "only-arm", "title": "ARM only", "arches": {"aarch64": {"url": "https://example.org/a.qcow2"}}}])
    monkeypatch.setattr(downloads, "host_arch", lambda: "x86_64")
    location = unquote(logged_in.post("/downloads/catalog/only-arm", follow_redirects=False).headers["location"])
    assert "нет версии для этого сервера (x86_64)" in location
    assert downloads.catalog_entries() == []
    assert "готовых образов в каталоге нет" in logged_in.get("/disk-images").text


def test_cancel_api_rules(logged_in, data_dirs):
    assert logged_in.post("/api/operations/00000000-0000-0000-0000-000000000000/cancel").status_code == 404
    other = core.new_operation("vm_create", "Создание VM x")
    response = logged_in.post(f"/api/operations/{other['id']}/cancel")
    assert response.status_code == 400 and "только скачивание" in response.json()["error"]
    finished = core.new_operation("download", "Скачивание x", download_kind="iso", filename="x.iso")
    core.finish_operation(finished, True, "Готово")
    assert logged_in.post(f"/api/operations/{finished['id']}/cancel").status_code == 409
    # операция без потока и без файла (панель перезапускалась) закрывается сразу
    running = core.new_operation("download", "Скачивание y", download_kind="disk", filename="y.qcow2")
    core.update_operation(running, status="running")
    response = logged_in.post(f"/api/operations/{running['id']}/cancel")
    operation = response.json()["operation"]
    assert response.status_code == 200 and operation["cancel_requested"] is True and operation["status"] == "error" and operation["cancelled"] is True
    assert not downloads.cancel_requested(running["id"])


def test_cancel_form_redirects_to_page(logged_in, data_dirs, www):
    stale = core.new_operation("download", "Скачивание y", download_kind="disk", filename="y.qcow2")
    core.update_operation(stale, status="running")
    response = logged_in.post(f"/downloads/{stale['id']}/cancel", follow_redirects=False)
    assert response.status_code == 303
    assert unquote(response.headers["location"]).startswith("/disk-images?download_message=Скачивание y.qcow2 отменено")
    assert core.read_operation(stale["id"])["status"] == "error"
    live = downloads.start_download(f"{www.url}/slow.iso", "iso")
    response = logged_in.post(f"/downloads/{live['id']}/cancel", follow_redirects=False)
    assert unquote(response.headers["location"]).startswith("/iso?download_message=Останавливаем скачивание slow.iso")
    assert wait_for(live["id"])["cancelled"] is True
    assert unquote(logged_in.post("/downloads/nope/cancel", follow_redirects=False).headers["location"]).startswith("/iso?download_error=")


def test_pages_show_download_cards(logged_in, data_dirs, monkeypatch):
    reads = []
    real = downloads.unfinished_downloads
    monkeypatch.setattr(downloads, "unfinished_downloads", lambda: reads.append(1) or real())
    iso_page = logged_in.get("/iso").text
    assert "Скачать по ссылке" in iso_page and 'name="kind" value="iso"' in iso_page
    assert "Скачиваем на сервер" not in iso_page
    assert "не нужно сначала загружать его на компьютер" in iso_page
    disk_page = logged_in.get("/disk-images").text
    assert "Каталог готовых систем" in disk_page and "Рекомендуем" in disk_page and 'name="kind" value="disk"' in disk_page
    assert "подходящие этому серверу" in disk_page
    assert len(reads) == 2  # каталог операций читается один раз на страницу, а не в каждой вставке
    running = core.new_operation("download", "Скачивание big.iso", download_kind="iso", filename="big.iso", url="https://example.org/big.iso", total_bytes=None)
    core.update_operation(running, status="running", progress=0, message="Подключаемся к серверу…")
    iso_page = logged_in.get("/iso?download_message=Скачиваем").text
    assert "Скачиваем на сервер" in iso_page and "big.iso" in iso_page and "indeterminate" in iso_page
    assert f"/downloads/{running['id']}/cancel" in iso_page and "Скачиваем</span>" in iso_page
    # отмена — не ошибка: без тоста при отправке формы, опрос показывает её как предупреждение
    assert 'data-toast="Скачивание отменено"' not in iso_page and "op.cancelled" in iso_page
    assert "big.iso" not in logged_in.get("/disk-images").text
