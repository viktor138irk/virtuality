"""vmops: увеличение диска, клонирование, пауза и спящий режим, заметки, живая нагрузка."""
import threading
from pathlib import Path
from urllib.parse import unquote

import pytest

import conftest
import core
import presenters
from features import vmops

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DOMSTATS = (FIXTURES / "domstats.txt").read_text()
GIB = 1024 ** 3


class Recorder:
    """Записывает команды; отвечает по overrides {(префикс,): результат|функция}, иначе как fake_run_cmd."""

    def __init__(self, overrides=None):
        self.calls: list[list[str]] = []
        self.overrides = overrides or {}

    def __call__(self, cmd, timeout=12, **kwargs):
        self.calls.append(list(cmd))
        for prefix, result in self.overrides.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                return result(cmd) if callable(result) else result
        return conftest.fake_run_cmd(cmd, timeout, **kwargs)

    def find(self, *prefix):
        return [call for call in self.calls if tuple(call[: len(prefix)]) == prefix]


@pytest.fixture()
def record(data_dirs, monkeypatch):
    """Подменяет run_cmd и в core (vmops), и в app (обзор, страница машины)."""
    import app

    recorder = Recorder()
    for module in (core, app):
        monkeypatch.setattr(module, "run_cmd", recorder)
    monkeypatch.setattr(vmops, "_samples", {})
    return recorder


@pytest.fixture()
def inline_operations(monkeypatch):
    """Фоновые операции выполняются сразу, в этом же потоке."""

    def run_now(operation, worker):
        core.update_operation(operation, status="running")
        worker(operation)

    monkeypatch.setattr(vmops, "run_operation", run_now)


def shut_off(record):
    record.overrides[("virsh", "domstate")] = conftest.fake_result("shut off")


def location(response) -> str:
    assert response.status_code == 303, response.text[:300]
    return unquote(response.headers["location"])


# ---------------------------------------------------------------- parsers
def test_parse_domstats_fixture():
    stats = vmops.parse_domstats(DOMSTATS)
    assert list(stats) == ["web01"]
    assert stats["web01"]["cpu.time"] == "48123456789"
    assert stats["web01"]["balloon.rss"] == "1534208"
    assert stats["web01"]["vcpu.current"] == "2"


def test_parse_domstats_several_domains():
    text = DOMSTATS + "Domain: 'db01'\n\nDomain: 'win 11'\n  cpu.time=5\n  vcpu.current=4\n"
    stats = vmops.parse_domstats(text)
    assert set(stats) == {"web01", "db01", "win 11"}
    assert stats["db01"] == {}
    assert stats["win 11"] == {"cpu.time": "5", "vcpu.current": "4"}


def test_compute_stats_cpu_percent_from_two_samples():
    samples: dict = {}
    first = vmops.compute_stats(vmops.parse_domstats(DOMSTATS), 100.0, samples)
    assert first["web01"]["cpu_pct"] is None  # первый замер — сравнивать не с чем
    assert first["web01"]["mem_used_mb"] == 1498 and first["web01"]["mem_total_mb"] == 2048
    later = DOMSTATS.replace("cpu.time=48123456789", "cpu.time=49123456789")  # +1 с процессорного времени
    second = vmops.compute_stats(vmops.parse_domstats(later), 101.0, samples)
    assert second["web01"]["cpu_pct"] == 50.0  # 1 с на 2 ядра за 1 с стены
    burst = DOMSTATS.replace("cpu.time=48123456789", "cpu.time=99123456789")
    third = vmops.compute_stats(vmops.parse_domstats(burst), 101.5, samples)
    assert third["web01"]["cpu_pct"] == 100.0  # не больше 100 %


def test_compute_stats_skips_shut_off_and_forgets_stale():
    samples = {"old": (1.0, 5)}
    stats = vmops.compute_stats({"db01": {}, "web01": {"cpu.time": "10", "balloon.current": "1024", "vcpu.current": "1"}}, 2.0, samples)
    assert set(stats) == {"web01"}
    assert stats["web01"]["mem_used_mb"] == 1 and stats["web01"]["mem_total_mb"] == 1
    assert set(samples) == {"web01"}


def test_parse_qemu_img_info():
    info = vmops.parse_qemu_img_info((FIXTURES / "qemu-img-info.json").read_text())
    assert info == {"virtual_size": 42949672960, "actual_size": 6443499520, "format": "qcow2"}
    assert vmops.parse_qemu_img_info("not json") == {}
    assert vmops.parse_qemu_img_info("[1, 2]") == {}


def test_parse_virsh_list_titles():
    titles = presenters.parse_virsh_list_titles((FIXTURES / "list-title.txt").read_text())
    assert titles == {"web01": "Сайт компании", "win11": "Бухгалтерия 1С"}
    assert presenters.parse_virsh_list_titles(" Id   Name    State\n---\n 1    web01   running") == {}
    assert presenters.parse_virsh_list_titles("") == {}


def test_parse_desc_output():
    assert vmops.parse_desc_output(conftest.fake_result("Сайт компании\n")) == "Сайт компании"
    assert vmops.parse_desc_output(conftest.fake_result("No title for domain: web01")) == ""
    assert vmops.parse_desc_output(conftest.fake_result(ok=False, stderr="error: failed to get domain")) == ""


def test_has_managed_save():
    assert vmops.has_managed_save(conftest.DOMINFO + "Managed save:   yes\n")
    assert not vmops.has_managed_save(conftest.DOMINFO + "Managed save:   no\n")
    assert not vmops.has_managed_save(conftest.DOMINFO)


def test_clean_notes():
    assert vmops.clean_title("  Сайт \n компании  ") == "Сайт компании"
    assert len(vmops.clean_title("x" * 200)) == vmops.TITLE_MAX
    assert vmops.clean_description("строка 1\r\nстрока 2  \r\n\r\n") == "строка 1\nстрока 2"


def test_clone_names_and_targets(data_dirs):
    assert vmops.suggest_clone_name("web01", {"web01"}) == "web01-copy"
    assert vmops.suggest_clone_name("web01", {"web01", "web01-copy", "web01-copy2"}) == "web01-copy3"
    disks = [
        {"device": "disk", "source": "/var/lib/virtuality/images/web01.qcow2"},
        {"device": "cdrom", "source": ""},
        {"device": "disk", "source": "/var/lib/virtuality/images/data.img"},
    ]
    targets = vmops.clone_targets("web02", disks)
    assert targets == [data_dirs["images"] / "web02.qcow2", data_dirs["images"] / "web02-disk2.img"]


def test_explain_error():
    assert "снимки" in vmops.explain_error("qemu-img: Can't resize an image which has snapshots", "x")
    assert vmops.explain_error("", "запасной текст") == "запасной текст"


# ---------------------------------------------------------------- live stats
def test_live_stats_requires_auth(client):
    assert client.get("/live/stats").status_code == 401


def test_live_stats_json(logged_in, record):
    first = logged_in.get("/live/stats").json()
    assert first["ok"] is True
    assert first["stats"]["web01"]["cpu_pct"] is None
    assert first["stats"]["web01"]["mem_used_mb"] == 1498 and first["stats"]["web01"]["mem_total_mb"] == 2048
    second = logged_in.get("/live/stats").json()
    assert second["stats"]["web01"]["cpu_pct"] == 0.0  # cpu.time в подделке не растёт
    assert record.find("virsh", "domstats") == [["virsh", "domstats", "--cpu-total", "--balloon", "--vcpu", "web01"]] * 2  # только активные машины, один вызов на запрос


def test_live_stats_without_active_vms(logged_in, record):
    record.overrides[("virsh", "list")] = conftest.fake_result("")
    assert logged_in.get("/live/stats").json()["stats"] == {}
    assert record.find("virsh", "domstats") == []


# ---------------------------------------------------------------- pages
def test_dashboard_shows_title_and_meters(logged_in, record):
    html = logged_in.get("/").text
    assert "Сайт компании" in html
    assert 'data-live-stats="web01"' in html and 'data-live-stats="db01"' in html
    assert "/vm/web01/ops/pause" in html and "/vm/web01/ops/hibernate" in html
    assert "/vm/web01/clone" in html and "/vm/db01/clone" in html
    assert record.find("virsh", "list", "--all", "--title")


def test_dashboard_paused_vm_offers_resume(logged_in, record):
    record.overrides[("virsh", "list")] = conftest.fake_result(" Id   Name    State\n-----------------------\n 2    web01   paused\n -    db01    shut off")
    html = logged_in.get("/").text
    assert 'action="/vm/web01/ops/resume"' in html
    assert 'action="/vm/web01/start"' not in html
    assert 'action="/vm/db01/start"' in html


def test_vm_page_shows_disks_notes_and_ops(logged_in, record):
    html = logged_in.get("/vm/web01").text
    assert "40 ГБ" in html and "занято 6.0 ГБ" in html
    assert 'action="/vm/web01/disks/vda/resize"' in html and 'min="41"' in html
    assert 'action="/vm/web01/disks/sda/resize"' not in html  # привод не увеличивают
    assert 'value="Сайт компании"' in html and "Nginx + база. Бэкап по пятницам." in html
    assert "Приостановить" in html and "Спящий режим" in html and "Клонировать" in html
    assert 'data-live-stats="web01"' in html  # карточка «Нагрузка сейчас»
    assert record.find("qemu-img", "info") == [["qemu-img", "info", "--output=json", "-U", str(core.IMAGES_DIR / "web01.qcow2")]]


def test_vm_page_paused_and_saved_states(logged_in, record):
    record.overrides[("virsh", "dominfo")] = conftest.fake_result(conftest.DOMINFO.replace("State:          running", "State:          paused"))
    html = logged_in.get("/vm/web01").text
    assert 'action="/vm/web01/ops/resume"' in html and "Продолжить" in html
    record.overrides[("virsh", "dominfo")] = conftest.fake_result(conftest.DOMINFO.replace("State:          running", "State:          shut off") + "Managed save:   yes\n")
    html = logged_in.get("/vm/web01").text
    assert "Машина в спящем режиме" in html and 'action="/vm/web01/ops/forget-save"' in html
    assert 'data-live-stats="web01"' not in html  # выключенная машина — нагрузки нет


def test_vm_page_messages(logged_in, record):
    html = logged_in.get("/vm/web01?disk_message=Диск+увеличен&notes_error=Не+вышло").text
    assert "Диск увеличен" in html and "Не вышло" in html


# ---------------------------------------------------------------- disk resize
def test_disk_resize_shut_off_uses_qemu_img(logged_in, record):
    shut_off(record)
    response = logged_in.post("/vm/web01/disks/vda/resize", data={"size_gb": "50"}, follow_redirects=False)
    assert location(response).startswith("/vm/web01?disk_message=Диск web01.qcow2 увеличен до 50 ГБ")
    assert location(response).endswith("#storage")
    assert record.find("qemu-img", "resize") == [["qemu-img", "resize", str(core.IMAGES_DIR / "web01.qcow2"), "50G"]]
    assert record.find("virsh", "blockresize") == []


def test_disk_resize_running_uses_blockresize(logged_in, record):
    response = logged_in.post("/vm/web01/disks/vda/resize", data={"size_gb": "41"}, follow_redirects=False)
    assert "disk_message" in location(response)
    assert record.find("virsh", "blockresize") == [["virsh", "blockresize", "web01", "vda", "41G"]]
    assert record.find("qemu-img", "resize") == []


def test_disk_resize_only_grows(logged_in, record):
    response = logged_in.post("/vm/web01/disks/vda/resize", data={"size_gb": "40"}, follow_redirects=False)
    assert "не меньше 41 ГБ" in location(response)
    response = logged_in.post("/vm/web01/disks/vda/resize", data={"size_gb": "999999"}, follow_redirects=False)
    assert "Слишком большой" in location(response)
    assert record.find("virsh", "blockresize") == [] and record.find("qemu-img", "resize") == []


def test_disk_resize_rejects_unknown_or_cdrom(logged_in, record):
    assert "Такого диска" in location(logged_in.post("/vm/web01/disks/vdz/resize", data={"size_gb": "50"}, follow_redirects=False))
    assert "нельзя увеличить" in location(logged_in.post("/vm/web01/disks/sda/resize", data={"size_gb": "50"}, follow_redirects=False))
    assert logged_in.post("/vm/web01/disks/../etc/resize", data={"size_gb": "50"}, follow_redirects=False).status_code in (400, 404)
    assert logged_in.post("/vm/-bad/disks/vda/resize", data={"size_gb": "50"}, follow_redirects=False).status_code == 400


def test_disk_resize_outside_storage_refused(logged_in, record):
    record.overrides[("virsh", "domblklist")] = conftest.fake_result((FIXTURES / "domblklist-details.txt").read_text().replace("/var/lib/virtuality/images", "/root"))
    assert "нельзя увеличить" in location(logged_in.post("/vm/web01/disks/vda/resize", data={"size_gb": "50"}, follow_redirects=False))


def test_disk_resize_checks_free_space(logged_in, record, monkeypatch):
    monkeypatch.setattr(vmops, "free_space", lambda path: 2 * GIB)
    response = logged_in.post("/vm/web01/disks/vda/resize", data={"size_gb": "50"}, follow_redirects=False)
    assert "свободно только 2.0 ГБ" in location(response)
    assert record.find("virsh", "blockresize") == []


def test_disk_resize_reports_tool_error(logged_in, record):
    record.overrides[("virsh", "blockresize")] = conftest.fake_result(ok=False, stderr="error: internal error: Can't resize an image which has snapshots")
    assert "удалите снимки" in location(logged_in.post("/vm/web01/disks/vda/resize", data={"size_gb": "50"}, follow_redirects=False))


# ---------------------------------------------------------------- pause / resume / hibernate
def test_ops_pause_and_resume(logged_in, record):
    response = logged_in.post("/vm/web01/ops/pause", data={"next": "/"}, follow_redirects=False)
    assert location(response).startswith("/?ops_message=Машина web01 приостановлена")
    assert record.find("virsh", "suspend") == [["virsh", "suspend", "web01"]]
    response = logged_in.post("/vm/web01/ops/resume", follow_redirects=False)
    assert "ops_error=Продолжить можно только приостановленную" in location(response)
    record.overrides[("virsh", "domstate")] = conftest.fake_result("paused")
    response = logged_in.post("/vm/web01/ops/resume", data={"next": "http://evil.example/"}, follow_redirects=False)
    assert location(response).startswith("/vm/web01?ops_message=")  # чужой next не используется
    assert record.find("virsh", "resume") == [["virsh", "resume", "web01"]]


def test_ops_validation(logged_in, record):
    assert logged_in.post("/vm/web01/ops/explode", follow_redirects=False).status_code == 400
    assert logged_in.post("/vm/-bad/ops/pause", follow_redirects=False).status_code == 400
    assert location(logged_in.post("/vm/ghost/ops/pause", follow_redirects=False)) == "/"
    shut_off(record)
    assert "ops_error=Приостановить можно только работающую" in location(logged_in.post("/vm/web01/ops/pause", follow_redirects=False))
    record.overrides[("virsh", "suspend")] = conftest.fake_result(ok=False, stderr="error: Timed out during operation: cannot acquire state change lock")
    record.overrides[("virsh", "domstate")] = conftest.fake_result("running")
    assert "занята другой операцией" in location(logged_in.post("/vm/web01/ops/pause", follow_redirects=False))


def test_ops_requires_login(client):
    assert client.post("/vm/web01/ops/pause", follow_redirects=False).headers["location"] == "/login"


def test_hibernate_runs_operation(logged_in, record, inline_operations, data_dirs):
    response = logged_in.post("/vm/web01/ops/hibernate", data={"next": "/"}, follow_redirects=False)
    assert location(response).startswith("/?ops_message=Машина web01 засыпает")
    assert record.find("virsh", "managedsave") == [["virsh", "managedsave", "web01"]]
    operations = core.list_operations(5)
    assert operations[0]["type"] == "vm_hibernate" and operations[0]["status"] == "success" and operations[0]["vm_name"] == "web01"


def test_hibernate_blocked(logged_in, record, monkeypatch):
    shut_off(record)
    assert "ops_error=В спящий режим можно" in location(logged_in.post("/vm/web01/ops/hibernate", follow_redirects=False))
    record.overrides[("virsh", "domstate")] = conftest.fake_result("running")
    monkeypatch.setattr(vmops, "free_space", lambda path: 0)
    assert "свободного места" in location(logged_in.post("/vm/web01/ops/hibernate", follow_redirects=False))
    assert record.find("virsh", "managedsave") == []


def test_hibernate_failure_is_reported(logged_in, record, inline_operations):
    record.overrides[("virsh", "managedsave")] = conftest.fake_result(ok=False, stderr="error: Requested operation is not valid: domain is not running")
    logged_in.post("/vm/web01/ops/hibernate", follow_redirects=False)
    operation = core.list_operations(1)[0]
    assert operation["status"] == "error" and "выключена" in operation["message"]


def test_forget_saved_state(logged_in, record):
    shut_off(record)
    response = logged_in.post("/vm/web01/ops/forget-save", follow_redirects=False)
    assert "ops_message=Сохранённое состояние удалено" in location(response)
    assert record.find("virsh", "managedsave-remove") == [["virsh", "managedsave-remove", "web01"]]


# ---------------------------------------------------------------- notes
def test_notes_save_running(logged_in, record):
    response = logged_in.post("/vm/web01/notes/save", data={"title": "  Сайт   компании ", "description": "строка 1\r\nстрока 2"}, follow_redirects=False)
    assert location(response) == "/vm/web01?notes_message=Заметки сохранены"
    assert record.find("virsh", "desc") == [
        ["virsh", "desc", "web01", "--title", "--new-desc=Сайт компании", "--config", "--live"],
        ["virsh", "desc", "web01", "--new-desc=строка 1\nстрока 2", "--config", "--live"],
    ]


def test_notes_save_shut_off_and_dashes(logged_in, record):
    shut_off(record)
    logged_in.post("/vm/web01/notes/save", data={"title": "--edit", "description": ""}, follow_redirects=False)
    calls = record.find("virsh", "desc")
    assert calls[0] == ["virsh", "desc", "web01", "--title", "--new-desc=--edit", "--config"]  # текст одним аргументом, без --live
    assert calls[1] == ["virsh", "desc", "web01", "--new-desc=", "--config"]  # пустое описание очищает


def test_notes_save_error(logged_in, record):
    record.overrides[("virsh", "desc")] = conftest.fake_result(ok=False, stderr="error: failed to get domain 'web01'")
    assert "notes_error=" in location(logged_in.post("/vm/web01/notes/save", data={"title": "x"}, follow_redirects=False))
    assert logged_in.post("/vm/-bad/notes/save", data={"title": "x"}, follow_redirects=False).status_code == 400


# ---------------------------------------------------------------- clone
def test_clone_page_blocked_while_running(logged_in, record):
    response = logged_in.get("/vm/web01/clone")
    assert response.status_code == 200
    assert "Сначала выключите машину" in response.text
    assert 'action="/vm/web01/shutdown"' in response.text
    assert "disabled" in response.text


def test_clone_page_ready(logged_in, record):
    shut_off(record)
    html = logged_in.get("/vm/web01/clone").text
    assert "Сначала выключите" not in html
    assert 'value="web01-copy"' in html
    assert "6.0 ГБ" in html  # нужно места — реальный размер диска
    assert "web01.qcow2" in html


def test_clone_validation(logged_in, record):
    shut_off(record)
    assert "Имя копии" in location(logged_in.post("/vm/web01/clone/start", data={"new_name": "bad name!"}, follow_redirects=False))
    assert "уже есть" in location(logged_in.post("/vm/web01/clone/start", data={"new_name": "db01"}, follow_redirects=False))
    assert "уже есть" in location(logged_in.post("/vm/web01/clone/start", data={"new_name": "WEB01"}, follow_redirects=False))
    record.overrides[("virsh", "domstate")] = conftest.fake_result("running")
    assert "Сначала выключите" in location(logged_in.post("/vm/web01/clone/start", data={"new_name": "web02"}, follow_redirects=False))
    assert record.find("virt-clone") == []
    assert logged_in.post("/vm/-bad/clone/start", data={"new_name": "web02"}, follow_redirects=False).status_code == 400


def test_clone_checks_space_and_existing_file(logged_in, record, data_dirs, monkeypatch):
    shut_off(record)
    (data_dirs["images"] / "web02.qcow2").write_bytes(b"x")
    assert "уже есть в хранилище" in location(logged_in.post("/vm/web01/clone/start", data={"new_name": "web02"}, follow_redirects=False))
    monkeypatch.setattr(vmops, "free_space", lambda path: GIB)
    assert "Не хватает места" in location(logged_in.post("/vm/web01/clone/start", data={"new_name": "web03"}, follow_redirects=False))


def test_clone_starts_operation(logged_in, record, inline_operations, data_dirs):
    shut_off(record)
    response = logged_in.post("/vm/web01/clone/start", data={"new_name": "web02"}, follow_redirects=False)
    assert location(response).startswith("/operations/")
    operation = core.read_operation(location(response).split("/")[-1])
    assert operation["status"] == "success" and operation["type"] == "vm_clone"
    assert operation["vm_name"] == "web02" and operation["source_vm"] == "web01"
    assert "web02 создана" in operation["message"]
    assert record.find("virt-clone") == [["virt-clone", "--original", "web01", "--name", "web02", "--file", str(data_dirs["images"] / "web02.qcow2")]]
    assert record.find("virsh", "pool-refresh") == [["virsh", "pool-refresh", "virtuality-images"]]
    assert "virt-clone --original web01" in operation["log_tail"]


def test_clone_failure_is_reported(logged_in, record, inline_operations):
    shut_off(record)
    record.overrides[("virt-clone",)] = conftest.fake_result(ok=False, stderr="ERROR    Storage volume already exists")
    response = logged_in.post("/vm/web01/clone/start", data={"new_name": "web02"}, follow_redirects=False)
    operation = core.read_operation(location(response).split("/")[-1])
    assert operation["status"] == "error" and "уже есть" in operation["message"]
    assert record.find("virsh", "pool-refresh") == []


def test_clone_progress_watcher(data_dirs):
    target = data_dirs["images"] / "copy.qcow2"
    target.write_bytes(b"\0" * (512 * 1024))
    operation = core.new_operation("vm_clone", "test", vm_name="copy", source_vm="web01")

    class OneTick:
        def __init__(self):
            self.ticks = 0

        def wait(self, timeout):
            self.ticks += 1
            return self.ticks > 1

    vmops._watch_clone_progress(operation, [target], 1024 * 1024, OneTick())
    fresh = core.read_operation(operation["id"])
    assert 3 <= fresh["progress"] <= 95 and "Копируем диски" in fresh["message"]
    assert vmops._allocated_bytes(data_dirs["images"] / "missing.qcow2") == 0


def test_clone_worker_stops_watcher(data_dirs, monkeypatch):
    """Даже если virt-clone упал, поток прогресса останавливается."""
    monkeypatch.setattr(core, "run_cmd", lambda cmd, timeout=12: conftest.fake_result(ok=False, stderr="boom"))
    operation = core.new_operation("vm_clone", "test", vm_name="web02", source_vm="web01", targets=[str(data_dirs["images"] / "web02.qcow2")], expected_bytes=10)
    before = threading.active_count()
    vmops.clone_worker(operation)
    assert threading.active_count() <= before
    assert core.read_operation(operation["id"])["status"] == "error"
